# SPDX-License-Identifier: Apache-2.0
"""The viewer node: a ROS consumer that also serves HTTP.

It subscribes to the simulator's topics like anybody else on the graph and
pushes what it hears to the page over a WebSocket. It only shows: whatever
a browser sends is read and dropped, and no route changes anything.

Two threads. ROS spins in one and turns messages into frames; the other
runs the HTTP server's event loop and owns every socket. A frame crosses
from the first to the second through `call_soon_threadsafe`, and nothing
else crosses.

The protocol is in this package's README.
"""

import asyncio
import base64
import contextlib
import errno
import faulthandler
import json
import math
import os
import re
import signal
import threading
import time
from pathlib import Path

import rclpy
from aiohttp import web
from ament_index_python.packages import get_package_share_directory
from fleetless_turtle_msgs.msg import TurtleStatus, World
from nav_msgs.msg import Odometry
from nav_msgs.msg import Path as PathMsg
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import BatteryState, LaserScan

NODE_NAME = "turtle_viewer"
BRIDGE_NODE_NAME = "fleetless_bridge"

DEFAULT_PORT = 8765
DEFAULT_CONFIG = "/opt/turtle/fleetless/turtle.fleetless.yaml"

HEALTHY_WITHIN_S = 2.0
"""`/healthz` answers 200 while a status message arrived within this long."""

BRIDGE_CHECK_PERIOD_S = 1.0
"""How often the node looks for the bridge on the graph."""

STOP_BOUND_S = 3.0
"""How long the node may take to end once it was asked to stop. Then it
says so and ends as it is. In the image, launch follows its interrupt with
SIGTERM a second later and with SIGKILL five seconds after that, and
`docker stop` allows ten in all: this is under both."""

STOP_LAST_RESORT_S = 4.0
"""How long until Python's faulthandler ends a stop that cannot say so
itself: one that hangs in a ROS call, which keeps Python's lock."""

LATCHED = QoSProfile(
    depth=1,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    reliability=ReliabilityPolicy.RELIABLE,
)


class Refusal(Exception):
    """A start the node refuses. The message is one sentence for the user.

    It names the environment variable before the parameter: whoever started
    the container set the one and has never heard of the other.
    """


def _number(value: float, digits: int) -> float | None:
    """A number a JSON frame can carry: rounded, and `null` where it is not finite."""
    return round(value, digits) if math.isfinite(value) else None


def _heading_deg(heading: float) -> float | None:
    """A heading in degrees with two decimals, in [0, 360). 359.996 is east, not 360.00."""
    degrees = _number(math.degrees(heading) % 360.0, 2)
    return 0.0 if degrees is not None and degrees >= 360.0 else degrees


def _frame(**fields) -> str:
    return json.dumps(fields, separators=(",", ":"), allow_nan=False)


def _points(path: PathMsg) -> list[list[float]]:
    """A path's positions as [x, y] in metres, to the millimetre.

    A pose that is no place is left out: JSON has no word for it.
    """
    points = []
    for pose in path.poses:
        x, y = pose.pose.position.x, pose.pose.position.y
        if math.isfinite(x) and math.isfinite(y):
            points.append([round(x, 3), round(y, 3)])
    return points


# --- The ROS side ------------------------------------------------------------


class ViewerNode(Node):
    """Subscribes like any consumer and hands every frame to `deliver`."""

    def __init__(self, deliver):
        super().__init__(NODE_NAME)
        # Any type is accepted and checked below: a launch file hands over
        # whatever an environment variable held.
        self.declare_parameter(
            "port",
            DEFAULT_PORT,
            ParameterDescriptor(
                description="The port the view listens on.", dynamic_typing=True, read_only=True
            ),
        )
        self.declare_parameter(
            "config",
            DEFAULT_CONFIG,
            ParameterDescriptor(description="The file behind /fleetless.yaml.", read_only=True),
        )
        self.port = self._port()
        self.config = Path(self.get_parameter("config").value)

        self._deliver = deliver
        self._status = None
        self._battery = None
        self._bridge = False
        self._last_status_at = None

        self.create_subscription(Odometry, "odom", self._on_odometry, 10)
        self.create_subscription(TurtleStatus, "turtle/status", self._on_status, 10)
        self.create_subscription(BatteryState, "battery_state", self._on_battery, 10)
        self.create_subscription(LaserScan, "scan", self._on_scan, 10)
        self.create_subscription(World, "turtle/world", self._on_world, LATCHED)
        self.create_subscription(PathMsg, "turtle/plan", self._on_plan, LATCHED)
        self.create_subscription(PathMsg, "turtle/trail", self._on_trail, LATCHED)
        self.create_timer(BRIDGE_CHECK_PERIOD_S, self._look_for_the_bridge)

    def _port(self) -> int:
        value = self.get_parameter("port").value
        try:
            if isinstance(value, bool | float):
                raise ValueError
            port = int(value)
            if not 1 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            raise Refusal(
                f'TURTLE_VIEW_PORT (the parameter port) is "{value}": '
                "the view listens on a whole number from 1 to 65535."
            ) from None
        return port

    def healthy(self) -> bool:
        """Whether a status message arrived within the last two seconds."""
        last = self._last_status_at
        return last is not None and time.monotonic() - last < HEALTHY_WITHIN_S

    def _on_status(self, status: TurtleStatus) -> None:
        self._status = status
        self._last_status_at = time.monotonic()

    def _on_battery(self, battery: BatteryState) -> None:
        self._battery = battery

    def _look_for_the_bridge(self) -> None:
        # All the view can know: the bridge publishes no connection state
        # into ROS. Whether the robot is connected is the console's to say.
        self._bridge = BRIDGE_NODE_NAME in self.get_node_names()

    def _on_odometry(self, odometry: Odometry) -> None:
        """One `state` frame per pose, once status and battery have been heard."""
        status, battery = self._status, self._battery
        if status is None or battery is None:
            return
        pose = odometry.pose.pose
        heading = 2.0 * math.atan2(pose.orientation.z, pose.orientation.w)
        self._deliver(
            "state",
            _frame(
                type="state",
                x=_number(pose.position.x, 4),
                y=_number(pose.position.y, 4),
                heading_deg=_heading_deg(heading),
                cell_x=status.cell_x,
                cell_y=status.cell_y,
                square=status.square,
                speed=_number(status.speed, 3),
                turn_rate=_number(status.turn_rate, 3),
                mode=status.mode,
                crawling=status.crawling,
                on_charger=status.on_charger,
                bumper=status.bumper,
                collisions=status.collisions,
                odometer_m=_number(status.odometer_m, 3),
                trail=status.trail,
                battery_percent=_number(battery.percentage * 100.0, 2),
                charging=battery.power_supply_status == BatteryState.POWER_SUPPLY_STATUS_CHARGING,
                bridge=self._bridge,
            ),
        )

    def _on_scan(self, scan: LaserScan) -> None:
        self._deliver(
            "scan",
            _frame(
                type="scan",
                angle_min=_number(scan.angle_min, 6),
                angle_increment=_number(scan.angle_increment, 6),
                # A ray that hit nothing is inf on the topic and null here.
                ranges=[_number(r, 3) for r in scan.ranges],
            ),
        )

    def _on_world(self, world: World) -> None:
        self._deliver(
            "world",
            _frame(
                type="world",
                width=world.width,
                height=world.height,
                resolution=_number(world.resolution, 6),
                kinds=base64.b64encode(bytes(world.kinds)).decode("ascii"),
            ),
        )

    def _on_plan(self, path: PathMsg) -> None:
        self._deliver("plan", _frame(type="plan", points=_points(path)))

    def _on_trail(self, path: PathMsg) -> None:
        self._deliver("trail", _frame(type="trail", points=_points(path)))


# --- The HTTP side -----------------------------------------------------------


class Client:
    """One browser. Per frame type only the newest waits, so a slow one never piles up."""

    def __init__(self, socket: web.WebSocketResponse):
        self.socket = socket
        self._waiting: dict[str, str] = {}
        self._something = asyncio.Event()

    def offer(self, kind: str, text: str) -> None:
        self._waiting[kind] = text
        self._something.set()

    async def send_forever(self) -> None:
        while True:
            await self._something.wait()
            self._something.clear()
            waiting, self._waiting = self._waiting, {}
            for text in waiting.values():
                await self.socket.send_str(text)


class Hub:
    """Every connected browser, and what a new one is told first."""

    # A new page needs these at once and cannot wait for the next change.
    REMEMBERED = ("world", "scan", "plan", "trail")

    def __init__(self):
        self.clients: set[Client] = set()
        self.remembered: dict[str, str] = {}

    def publish(self, kind: str, text: str) -> None:
        if kind in self.REMEMBERED:
            self.remembered[kind] = text
        for client in self.clients:
            client.offer(kind, text)


MOST_ORIGINS_NAMED = 100
"""How many refused origins the log names. The ones after are refused without a line."""

# ASCII and nothing else, in both patterns. A browser writes a scheme, a host
# and a port in ASCII, a name with other letters in its `xn--` form. And a
# pattern that ignores case the Unicode way takes U+017F, the long s, for the
# `s` of `https`.
_HOST_AND_PORT = (
    r"(?P<host>\[[0-9A-Fa-f:.]+\]|[^\s/?#@:\[\]\x80-\U0010ffff]+)(?::(?P<port>[0-9]{1,5}))?"
)
_ORIGIN = re.compile(r"http(?P<secure>s)?://" + _HOST_AND_PORT, re.IGNORECASE | re.ASCII)
_HOST = re.compile(_HOST_AND_PORT, re.ASCII)


def names_this_view(origin: str, host: str | None) -> bool:
    """Whether an `Origin` header names the place its request was sent to.

    A browser puts the page that opens a WebSocket into `Origin` and the
    place it connects to into `Host`. For the view's own page the two name
    the same host and port, on whatever port Docker maps the view to. The
    scheme is not compared: a proxy may end TLS in front of the view. A side
    that names no port has the default one of the origin's scheme, which is
    what a browser leaves out on both sides. What is no `http` or `https`
    origin (`null`, an empty header, anything with a path, anything that is
    not ASCII) names nothing.
    """
    claimed = _ORIGIN.fullmatch(origin)
    asked = _HOST.fullmatch(host) if host is not None else None
    if claimed is None or asked is None:
        return False
    # Nothing below can fail on what a client wrote: the groups are the
    # patterns' own, a host is ASCII, and a port is one to five digits.
    default = 443 if claimed["secure"] else 80
    return (claimed["host"].lower(), int(claimed["port"] or default)) == (
        asked["host"].lower(),
        int(asked["port"] or default),
    )


def _quoted(header: str) -> str:
    """A header's value for a log line or an answer: in quotes, ASCII, 200 characters at most."""
    return json.dumps(header if len(header) <= 200 else header[:200] + "...")


def build_app(hub: Hub, healthy, web_dir: Path, config: Path) -> web.Application:
    async def index(_request: web.Request) -> web.StreamResponse:
        return web.FileResponse(web_dir / "index.html")

    async def healthz(_request: web.Request) -> web.Response:
        if healthy():
            return web.Response(text="ok\n")
        return web.Response(status=503, text="No status from the turtle within 2 s.\n")

    async def fleetless_yaml(_request: web.Request) -> web.StreamResponse:
        if not config.is_file():
            raise web.HTTPNotFound(text=f"{config} is not in this container.\n")
        return web.FileResponse(
            config,
            headers={
                "Content-Type": "application/yaml",
                "Content-Disposition": 'attachment; filename="turtle.fleetless.yaml"',
            },
        )

    # The origins the log has named. A refused page tries again every few
    # seconds, and one site can ask under any number of names.
    named: set[str] = set()

    def turn_away(origins: list[str], hosts: list[str]) -> web.Response:
        origin = _quoted(", ".join(origins))
        if origin not in named and len(named) < MOST_ORIGINS_NAMED:
            named.add(origin)
            log = rclpy.logging.get_logger(NODE_NAME)
            log.warning(
                f"A page of {origin} asked for the turtle's state at "
                f"{_quoted(', '.join(hosts))} and was refused: "
                "the view's WebSocket answers its own page only."
            )
            if len(named) == MOST_ORIGINS_NAMED:
                log.warning(
                    f"{MOST_ORIGINS_NAMED} origins were refused: the log names no further ones."
                )
        return web.Response(
            status=403,
            text="The view's WebSocket answers its own page only, "
            f"and this request names another origin: {origin}.\n",
        )

    async def websocket(request: web.Request) -> web.StreamResponse:
        # A browser names the page that opens a WebSocket in `Origin`, and
        # the page cannot change that. Only the view's own page gets the
        # turtle's state. A request without the header is no browser's: a
        # script, a test.
        origins = request.headers.getall("Origin", [])
        hosts = request.headers.getall("Host", [])
        if origins and not (
            len(origins) == len(hosts) == 1 and names_this_view(origins[0], hosts[0])
        ):
            return turn_away(origins, hosts)
        socket = web.WebSocketResponse(heartbeat=20.0)
        await socket.prepare(request)
        client = Client(socket)
        for kind in Hub.REMEMBERED:
            if kind in hub.remembered:
                client.offer(kind, hub.remembered[kind])
        hub.clients.add(client)
        sender = asyncio.create_task(client.send_forever())
        try:
            # The view only shows. What a browser sends is read, so the
            # socket stays healthy, and dropped.
            async for _ in socket:
                pass
        finally:
            hub.clients.discard(client)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                await sender
        return socket

    @web.middleware
    async def always_fresh(request: web.Request, handler) -> web.StreamResponse:
        response = await handler(request)
        # The page is served from the image it belongs to. A browser that
        # kept yesterday's script would talk to today's node.
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    async def say_goodbye(_app: web.Application) -> None:
        # An open WebSocket would hold the server open long past the ten
        # seconds `docker stop` allows.
        for client in list(hub.clients):
            await client.socket.close(code=1001, message=b"The view is stopping.")

    app = web.Application(middlewares=[always_fresh])
    app.on_shutdown.append(say_goodbye)
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/fleetless.yaml", fleetless_yaml)
    # The page's own files, beside it. Last, so the routes above win.
    app.router.add_static("/", web_dir)
    return app


async def serve(node: ViewerNode, hub: Hub, stop: asyncio.Event) -> None:
    if stop.is_set():
        return  # asked to stop before there was anything to serve
    web_dir = Path(get_package_share_directory("fleetless_turtle_viewer")) / "web"
    runner = web.AppRunner(
        build_app(hub, node.healthy, web_dir, node.config), access_log=None, shutdown_timeout=2.0
    )
    await runner.setup()
    try:
        try:
            await web.TCPSite(runner, "0.0.0.0", node.port).start()
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                raise Refusal(
                    f"Port {node.port} is in use: the view cannot listen there."
                ) from None
            raise Refusal(
                f"The view cannot listen on port {node.port}: {error.strerror}."
            ) from None
        node.get_logger().info(f"The view listens on port {node.port}.")
        await stop.wait()
    finally:
        await runner.cleanup()


def hear_a_stop(stop: threading.Event) -> None:
    """Take a request to stop. Whoever hears one calls this, on the thread that heard it.

    It arms the faulthandler first and sets `stop` after, so the last resort
    stands before anybody knows of the stop and before a line of it runs.
    The thread that waits for `stop` could not see to that: once woken it
    still needs Python's lock, and on a busy machine the stop reaches a ROS
    call that keeps the lock before the thread got it. Then it never runs.

    The bound counts from the first request: a second one, as launch's
    SIGTERM is, arms nothing.
    """
    if stop.is_set():
        return
    faulthandler.dump_traceback_later(STOP_LAST_RESORT_S, exit=True, file=2)
    stop.set()


def _hold_a_stop_to_its_bound(stop: threading.Event, ended: threading.Event) -> None:
    """Wait for the stop, then give the node `STOP_BOUND_S` to end. A thread of its own runs this.

    What ends a node is ROS's to do, once the HTTP server has closed, and
    its calls take no bound: one that does not return would keep the process
    until launch or Docker kills it. So from the moment a stop is asked for,
    two things watch, the server's last seconds included.

    This thread. When the bound runs out it says one sentence and ends the
    process with exit code 0: it was asked to stop, and it stops. It writes
    the sentence to stderr itself, because ROS's logger may be what hangs.

    And Python's faulthandler, a second later. A ROS call that hangs keeps
    Python's lock, and then no Python thread runs again, this one included.
    The faulthandler's own thread needs no lock: it writes where every
    thread stands, which names the call, and ends the process with exit
    code 1. `hear_a_stop` has armed it by the time this thread is woken.

    A stop that ends in time takes the faulthandler's bound away and sets
    `ended`, and neither says anything.
    """
    stop.wait()
    if ended.wait(STOP_BOUND_S):
        return
    words = f"The view did not finish stopping within {STOP_BOUND_S:g} s and ends without it."
    os.write(2, f"{words}\n".encode())
    os._exit(0)


def run(stop: threading.Event, args=None) -> int:
    """Run the node until a stop arrives. `start.py` hands SIGINT and SIGTERM to `hear_a_stop`."""
    if stop.is_set():
        return 0  # asked to stop during the imports: there is nothing to undo
    ended = threading.Event()
    threading.Thread(
        target=_hold_a_stop_to_its_bound, args=(stop, ended), name="stop", daemon=True
    ).start()
    # This process takes the signals itself, so ROS must not.
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    loop = asyncio.new_event_loop()
    stopping = asyncio.Event()
    hub = Hub()

    def deliver(kind: str, text: str) -> None:
        """Called on the ROS thread; the hub lives on the event loop's."""
        with contextlib.suppress(RuntimeError):  # the loop has closed: we are stopping
            loop.call_soon_threadsafe(hub.publish, kind, text)

    try:
        node = ViewerNode(deliver)
    except Refusal as refusal:
        rclpy.logging.get_logger(NODE_NAME).fatal(str(refusal))
        rclpy.try_shutdown()
        loop.close()
        return 2
    if stop.is_set():
        # Asked to stop while it was still starting, by a person or by a
        # launch whose other node gave up. Nothing is wrong: leave quietly.
        node.destroy_node()
        rclpy.try_shutdown()
        loop.close()
        faulthandler.cancel_dump_traceback_later()
        ended.set()
        return 0

    executor = SingleThreadedExecutor()
    executor.add_node(node)

    def spin() -> None:
        with contextlib.suppress(ExternalShutdownException):
            executor.spin()

    spinning = threading.Thread(target=spin, name="ros", daemon=True)
    spinning.start()

    # From here on a stop wakes the event loop, which hears it here. One
    # that came a moment earlier has set the flag, which is why the flag is
    # read after.
    def on_stop() -> None:
        hear_a_stop(stop)  # before the server closes: its last seconds are under the bound
        stopping.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, on_stop)
    if stop.is_set():
        stopping.set()
    code = 0
    try:
        loop.run_until_complete(serve(node, hub, stopping))
    except Refusal as refusal:
        node.get_logger().fatal(str(refusal))
        code = 2
    finally:
        executor.shutdown()
        spinning.join(timeout=5.0)
        node.destroy_node()
        rclpy.try_shutdown()
        loop.close()
        faulthandler.cancel_dump_traceback_later()
        ended.set()
    return code

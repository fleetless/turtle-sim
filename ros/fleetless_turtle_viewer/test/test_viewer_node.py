# SPDX-License-Identifier: Apache-2.0
"""The viewer node against a fake turtle: the routes, the frames, `/healthz`
in both states, and that nothing a browser does changes anything."""

import asyncio
import base64
import json
import math
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import aiohttp
import pytest
import rclpy
from ament_index_python.packages import get_package_prefix
from fleetless_turtle_msgs.msg import TurtleStatus, World
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, LaserScan

VIEWER = Path(get_package_prefix("fleetless_turtle_viewer")) / "lib/fleetless_turtle_viewer/viewer"
PORT = 18765
URL = f"http://127.0.0.1:{PORT}"

LATCHED = QoSProfile(
    depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE
)

# Row-major from cell (0, 0): a wall, a rock, the pad, and water in the far corner.
KINDS = bytearray(256 * 256)
KINDS[0] = World.WALL
KINDS[257] = World.ROCK
KINDS[8 * 256 + 8] = World.PAD
KINDS[-1] = World.WATER

STATE_KEYS = {
    "type",
    "x",
    "y",
    "heading_deg",
    "cell_x",
    "cell_y",
    "square",
    "speed",
    "turn_rate",
    "mode",
    "crawling",
    "on_charger",
    "bumper",
    "collisions",
    "odometer_m",
    "trail",
    "battery_percent",
    "charging",
    "bridge",
}


class FakeTurtle:
    """Publishes what the simulator would, with values a test can recognise."""

    def __init__(self):
        self.node = rclpy.create_node("fake_turtle")
        self.talking = False
        self._odometry = self.node.create_publisher(Odometry, "/odom", 10)
        self._status = self.node.create_publisher(TurtleStatus, "/turtle/status", 10)
        self._battery = self.node.create_publisher(BatteryState, "/battery_state", 10)
        self._scan = self.node.create_publisher(LaserScan, "/scan", 10)
        self._world = self.node.create_publisher(World, "/turtle/world", LATCHED)
        self.node.create_timer(1 / 20, self._say_odometry)
        self.node.create_timer(1 / 10, self._say_status)
        self.node.create_timer(1 / 5, self._say_battery_and_scan)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self.node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._thread.start()

        world = World()
        world.header.frame_id = "map"
        world.width = world.height = 256
        world.resolution = 0.1
        world.kinds = bytes(KINDS)
        self._world.publish(world)

    def close(self):
        self._executor.shutdown()
        self._thread.join(timeout=5.0)
        self.node.destroy_node()

    def _say_odometry(self):
        if not self.talking:
            return
        odometry = Odometry()
        odometry.pose.pose.position.x = 1.25
        odometry.pose.pose.position.y = 4.05
        # Heading north: a quarter turn counter-clockwise.
        odometry.pose.pose.orientation.z = math.sin(math.pi / 4)
        odometry.pose.pose.orientation.w = math.cos(math.pi / 4)
        self._odometry.publish(odometry)

    def _say_status(self):
        if not self.talking:
            return
        status = TurtleStatus()
        status.cell_x, status.cell_y, status.square = 12, 40, "A3"
        status.heading_deg = 90.0
        status.speed, status.turn_rate = 0.25, -0.5
        status.mode = "teleop"
        status.crawling, status.on_charger, status.bumper, status.trail = False, True, True, False
        status.collisions = 3
        status.odometer_m = 12.5
        self._status.publish(status)

    def _say_battery_and_scan(self):
        if not self.talking:
            return
        battery = BatteryState()
        battery.percentage = 0.42
        battery.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_CHARGING
        self._battery.publish(battery)
        scan = LaserScan()
        scan.angle_min = -math.pi
        scan.angle_increment = math.radians(5)
        scan.ranges = [1.25, math.inf, 0.4] + [math.inf] * 69
        self._scan.publish(scan)


def start_viewer(*parameters: str) -> subprocess.Popen:
    arguments = [str(VIEWER), "--ros-args"]
    for parameter in parameters:
        arguments += ["-p", parameter]
    return subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def stop_viewer(process: subprocess.Popen, how=signal.SIGINT) -> str:
    process.send_signal(how)
    try:
        said, _ = process.communicate(timeout=10.0)
    finally:
        if process.poll() is None:
            process.kill()
    return said


def get(path: str, method: str = "GET"):
    """Status, headers and body of one request to the view."""
    request = urllib.request.Request(URL + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def wait_until(condition, timeout: float = 10.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"{what} did not happen within {timeout} s.")


def frames(seconds: float, say: list | None = None, until=None) -> list[dict]:
    """Connect as a browser would and return the frames of the next `seconds`.

    `say` is sent first, text or bytes; `until` ends the listening early.
    """

    async def listen():
        heard = []
        async with aiohttp.ClientSession() as session, session.ws_connect(URL + "/ws") as socket:
            for words in say or []:
                await (
                    socket.send_bytes(words) if isinstance(words, bytes) else socket.send_str(words)
                )
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    message = await socket.receive(timeout=deadline - time.monotonic())
                except TimeoutError:
                    break
                assert message.type == aiohttp.WSMsgType.TEXT, message
                heard.append(json.loads(message.data))
                if until is not None and until(heard[-1]):
                    break
        return heard

    return asyncio.run(listen())


@pytest.fixture(scope="module")
def turtle():
    rclpy.init()
    fake = FakeTurtle()
    yield fake
    fake.close()
    rclpy.shutdown()


@pytest.fixture(scope="module")
def config(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("config") / "turtle.fleetless.yaml"
    path.write_text("fleetless: 1\n# the shipped file would stand here\n")
    return path


@pytest.fixture(scope="module")
def view(turtle, config):
    """A running viewer node. The fake turtle is on the graph and silent."""
    process = start_viewer(f"port:={PORT}", f"config:={config}")
    try:
        wait_until(lambda: answers("/healthz"), timeout=30.0, what="the view answering")
        yield process
    finally:
        said = stop_viewer(process) if process.poll() is None else process.communicate()[0]
    assert process.returncode == 0, said
    assert "Traceback" not in said, said
    assert f"The view listens on port {PORT}." in said


def answers(path: str) -> bool:
    try:
        get(path)
    except OSError:
        return False
    return True


# The tests run in the order they stand in: the turtle starts silent, talks,
# and falls silent again.


def test_healthz_is_503_before_the_turtle_says_anything(view):
    status, _, body = get("/healthz")
    assert status == 503
    assert body == b"No status from the turtle within 2 s.\n"


def test_the_page_and_its_files_are_served(view):
    status, headers, body = get("/")
    assert status == 200 and headers.get_content_type() == "text/html"
    assert b"<title>turtle-sim</title>" in body
    assert headers["Cache-Control"] == "no-cache"
    # A browser runs a module only if it is served as JavaScript.
    javascript = {"text/javascript", "application/javascript"}
    for path, content_types in [
        ("/main.js", javascript),
        ("/scene.js", javascript),
        ("/style.css", {"text/css"}),
        ("/vendor/three.module.js", javascript),
        ("/vendor/three.core.js", javascript),
        ("/vendor/OrbitControls.js", javascript),
    ]:
        status, headers, body = get(path)
        assert status == 200, path
        assert headers.get_content_type() in content_types, path
        assert len(body) > 100, path
    assert get("/no-such-file.js")[0] == 404
    assert get("/../package.xml")[0] in (403, 404)


def test_fleetless_yaml_is_the_shipped_file_as_a_download(view, config):
    status, headers, body = get("/fleetless.yaml")
    assert status == 200
    assert body == config.read_bytes()
    assert headers.get_content_type() == "application/yaml"
    assert headers["Content-Disposition"] == 'attachment; filename="turtle.fleetless.yaml"'


def test_no_route_changes_anything(view):
    for path in ["/", "/ws", "/healthz", "/fleetless.yaml", "/main.js"]:
        for method in ["POST", "PUT", "DELETE", "PATCH"]:
            assert get(path, method=method)[0] == 405, f"{method} {path}"


def test_healthz_is_200_while_the_turtle_talks(view, turtle):
    turtle.talking = True
    wait_until(lambda: get("/healthz")[0] == 200, what="/healthz answering 200")
    assert get("/healthz")[2] == b"ok\n"


def test_a_new_page_is_sent_the_world_first(view, turtle):
    heard = frames(2.0, until=lambda frame: frame["type"] == "world")
    world = heard[0]
    assert world["type"] == "world"
    assert (world["width"], world["height"], world["resolution"]) == (256, 256, 0.1)
    assert set(world) == {"type", "width", "height", "resolution", "kinds"}
    assert base64.b64decode(world["kinds"]) == bytes(KINDS)


def test_state_frames_carry_pose_status_battery_and_bridge(view, turtle):
    heard = frames(2.0)
    states = [frame for frame in heard if frame["type"] == "state"]
    # 20 Hz, one per pose; a busy machine may lose a few.
    assert 25 <= len(states) <= 45
    state = states[-1]
    assert set(state) == STATE_KEYS
    assert (state["x"], state["y"], state["heading_deg"]) == (1.25, 4.05, 90.0)
    assert (state["cell_x"], state["cell_y"], state["square"]) == (12, 40, "A3")
    assert (state["speed"], state["turn_rate"], state["mode"]) == (0.25, -0.5, "teleop")
    assert (state["crawling"], state["on_charger"], state["bumper"], state["trail"]) == (
        False,
        True,
        True,
        False,
    )
    assert (state["collisions"], state["odometer_m"]) == (3, 12.5)
    assert (state["battery_percent"], state["charging"]) == (42.0, True)
    assert state["bridge"] is False


def test_scan_frames_carry_null_where_a_ray_hit_nothing(view, turtle):
    heard = frames(1.5)
    scans = [frame for frame in heard if frame["type"] == "scan"]
    assert len(scans) >= 4
    scan = scans[-1]
    assert set(scan) == {"type", "angle_min", "angle_increment", "ranges"}
    assert scan["angle_min"] == pytest.approx(-math.pi, abs=1e-5)
    assert scan["angle_increment"] == pytest.approx(math.radians(5), abs=1e-5)
    assert len(scan["ranges"]) == 72
    assert scan["ranges"][:3] == [1.25, None, 0.4]


def test_every_frame_is_of_a_documented_type(view, turtle):
    assert {frame["type"] for frame in frames(1.0)} == {"world", "state", "scan"}


def test_what_a_browser_sends_is_ignored(view, turtle):
    orders = [
        "drive north",
        json.dumps({"type": "cmd_vel", "linear": 0.5}),
        json.dumps({"type": "state", "x": 0}),
        b"\x00\x01\x02",
        "x" * 100_000,
    ]
    heard = frames(1.5, say=orders)
    # The frames go on as if nothing had been said, and none answers.
    assert {frame["type"] for frame in heard} == {"world", "state", "scan"}
    assert sum(frame["type"] == "state" for frame in heard) >= 15
    # And the node has no way to pass an order on: it publishes nothing of its own.
    published = dict(turtle.node.get_publisher_names_and_types_by_node("turtle_viewer", "/"))
    assert set(published) <= {"/rosout", "/parameter_events"}
    assert turtle.node.count_subscribers("/cmd_vel") == 0


def test_the_bridge_flag_follows_a_node_named_fleetless_bridge(view, turtle):
    bridge = rclpy.create_node("fleetless_bridge")
    try:
        heard = frames(5.0, until=lambda frame: frame.get("bridge") is True)
        assert heard[-1].get("bridge") is True
    finally:
        bridge.destroy_node()
    heard = frames(5.0, until=lambda frame: frame.get("bridge") is False)
    assert heard[-1].get("bridge") is False


def test_a_second_node_on_the_same_port_is_refused(view):
    second = start_viewer(f"port:={PORT}")
    said, _ = second.communicate(timeout=30.0)
    lines = [line for line in said.splitlines() if line.strip()]
    assert second.returncode == 2, said
    assert "Traceback" not in said, said
    assert len(lines) == 1, said
    assert "[FATAL]" in lines[0] and "[turtle_viewer]" in lines[0]
    assert lines[0].endswith(f"Port {PORT} is in use: the view cannot listen there.")
    assert get("/healthz")[0] == 200  # the first one never noticed


@pytest.mark.parametrize("value", ["abc", "0", "70000", "8765.5", "-1", "true"])
def test_a_port_that_is_no_port_is_refused(value):
    refused = start_viewer(f"port:={value}")
    said, _ = refused.communicate(timeout=30.0)
    lines = [line for line in said.splitlines() if line.strip()]
    assert refused.returncode == 2, said
    assert "Traceback" not in said, said
    assert len(lines) == 1, said
    assert 'port is "' in lines[0]
    assert lines[0].endswith("the view listens on a whole number from 1 to 65535.")


def test_healthz_is_503_again_once_the_turtle_falls_silent(view, turtle):
    assert get("/healthz")[0] == 200
    turtle.talking = False
    silent_since = time.monotonic()
    wait_until(lambda: get("/healthz")[0] == 503, timeout=5.0, what="/healthz answering 503")
    assert 1.5 < time.monotonic() - silent_since < 3.5
    # The page is still served; it is the turtle that is gone.
    assert get("/")[0] == 200
    assert [frame for frame in frames(1.0) if frame["type"] == "state"] == []


def test_a_connected_browser_does_not_keep_the_node_from_stopping(turtle, config):
    port = PORT + 1
    process = start_viewer(f"port:={port}", f"config:={config}")
    closed = {}

    async def browse():
        async with aiohttp.ClientSession() as session:
            for _ in range(200):
                try:
                    socket = await session.ws_connect(f"http://127.0.0.1:{port}/ws")
                    break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.1)
            else:
                raise AssertionError("The second view never answered.")
            try:
                process.send_signal(signal.SIGTERM)
                asked = time.monotonic()
                while True:
                    message = await socket.receive(timeout=10.0)
                    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                        closed["code"] = socket.close_code
                        closed["after"] = time.monotonic() - asked
                        break
            finally:
                await socket.close()

    try:
        asyncio.run(browse())
        said, _ = process.communicate(timeout=10.0)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, said
    assert "Traceback" not in said, said
    assert closed["code"] == 1001  # going away
    assert closed["after"] < 5.0

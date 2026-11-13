# SPDX-License-Identifier: Apache-2.0
"""The cameras node: a thin adapter between the two renderers and ROS.

It subscribes like anybody else on the graph: `/odom` says where the turtle
is, `/turtle/world` what the cells are, `/turtle/plan` and `/turtle/trail`
where it is going and where it has been. Ten times a second it has `top`
and `eye` drawn from what it last heard and publishes both as `rgb8`
images. It draws nothing itself; `render/top.py` and `render/eye.py` are the
files to read for that.

It is a node of its own so that a picture never delays a step of the
simulation. A camera nobody subscribes to is not drawn.

Anybody on the graph may publish what this node reads. So it looks at a
world and a pose before it takes them: what the renderers cannot draw is
refused here, with one line in the log, and never reaches them.
"""

import array
import faulthandler
import math
import os
import threading

import numpy as np
import rclpy
from fleetless_turtle_msgs.msg import World
from nav_msgs.msg import Odometry, Path
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import Image

from .render import constants as look
from .render import eye, top
from .render.scene import Scene

NODE_NAME = "turtle_cameras"

STOP_BOUND_S = 3.0
"""How long the node may take to end once it was asked to stop. Then it
says so and ends as it is. In the image, launch follows its interrupt with
SIGTERM a second later and with SIGKILL five seconds after that, and
`docker stop` allows ten in all: this is under both."""

STOP_LAST_RESORT_S = 4.0
"""How long until Python's faulthandler ends a stop that cannot say so
itself: one that hangs in a ROS call, which keeps Python's lock."""

# "Latched": a subscriber that joins late still gets the last message.
LATCHED = QoSProfile(
    depth=1,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    reliability=ReliabilityPolicy.RELIABLE,
)

# Reliable, so the bridge's subscription matches, and only the newest
# picture is kept: a subscriber that falls behind skips ahead.
PICTURES = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)


def _points(path: Path) -> np.ndarray:
    """A path's poses as rows of (x, y).

    A point that cannot be drawn is left out: one that is no number, which
    fails every comparison, or one too far from the map's origin.
    """
    points = np.array([(p.pose.position.x, p.pose.position.y) for p in path.poses], float)
    points = points.reshape(-1, 2)
    return points[(np.abs(points) <= look.POSE_LIMIT_M).all(axis=1)]


def _fault_of_a_world(width: int, height: int, cells: int) -> tuple[str, str] | None:
    """Why a world cannot be drawn: the sort of fault and the words for it. None if it can."""
    if cells != width * height:
        return "count", f"came with {cells} of them"
    if cells == 0:
        return "empty", "has none"
    if max(width, height) > look.WORLD_CELLS_LIMIT:
        return "size", f"is more than the cameras draw, {look.WORLD_CELLS_LIMIT} along a side"
    return None


class CamerasNode(Node):
    def __init__(self):
        super().__init__(NODE_NAME)
        # What was last heard. A picture is drawn once the first two are known.
        self._kinds = None
        self._pose = None
        self._plan = np.empty((0, 2))
        self._trail = np.empty((0, 2))
        self._refused = set()

        # Each camera: its publisher, its renderer and the frame its picture is in.
        self._cameras = [
            (self.create_publisher(Image, "turtle/camera/top/image_raw", PICTURES), top, "map"),
            (
                self.create_publisher(Image, "turtle/camera/eye/image_raw", PICTURES),
                eye,
                "base_link",
            ),
        ]

        self.create_subscription(Odometry, "odom", self._on_odometry, 10)
        self.create_subscription(World, "turtle/world", self._on_world, LATCHED)
        # Empty until a goal runs or the trail is switched on, and unheard
        # of on a graph where nobody publishes them: both are "nothing to draw".
        self.create_subscription(Path, "turtle/plan", self._on_plan, LATCHED)
        self.create_subscription(Path, "turtle/trail", self._on_trail, LATCHED)

        self.create_timer(1.0 / look.FRAME_RATE_HZ, self._publish)
        self.get_logger().info(
            f"The cameras draw top ({look.TOP_SIZE_PX} x {look.TOP_SIZE_PX}) and "
            f"eye ({look.EYE_WIDTH_PX} x {look.EYE_HEIGHT_PX}), "
            f"{look.FRAME_RATE_HZ:g} pictures a second each."
        )

    # --- In ------------------------------------------------------------------

    def _refuse(self, sort: str, sentence: str) -> None:
        """Say once that an input of this sort was refused. The pictures go on from what was."""
        if sort not in self._refused:
            self._refused.add(sort)
            self.get_logger().warning(f"{sentence} This is logged once.")

    def _on_odometry(self, odometry: Odometry) -> None:
        pose = odometry.pose.pose
        x, y = pose.position.x, pose.position.y
        heading = 2.0 * math.atan2(pose.orientation.z, pose.orientation.w)
        limit = look.POSE_LIMIT_M
        # A value that is no number fails every comparison, so it is refused
        # together with what lies too far out.
        if not (abs(x) <= limit and abs(y) <= limit and math.isfinite(heading)):
            self._refuse(
                "pose",
                f"An /odom pose of x {x:g} m, y {y:g} m, heading {heading:g} rad is not taken: "
                f"the cameras draw from numbers, up to {limit:g} m from the map's origin.",
            )
            return
        self._pose = (x, y, heading)

    def _on_world(self, world: World) -> None:
        width, height, cells = world.width, world.height, len(world.kinds)
        fault = _fault_of_a_world(width, height, cells)
        if fault is not None:
            sort, words = fault
            self._refuse(
                f"world, {sort}",
                f"A /turtle/world of {width} x {height} cells {words}. It is not drawn.",
            )
            return
        # A copy of its own: the renderers keep what they drew from it.
        self._kinds = np.array(world.kinds, np.uint8).reshape(height, width)

    def _on_plan(self, plan: Path) -> None:
        self._plan = _points(plan)

    def _on_trail(self, trail: Path) -> None:
        self._trail = _points(trail)

    # --- Out -----------------------------------------------------------------

    def _publish(self) -> None:
        if self._kinds is None or self._pose is None:
            return
        # Drawing costs; a picture nobody asked for is not drawn.
        wanted = [camera for camera in self._cameras if camera[0].get_subscription_count() > 0]
        if not wanted:
            return
        x, y, heading = self._pose
        scene = Scene(self._kinds, x, y, heading, plan=self._plan, trail=self._trail)
        stamp = self.get_clock().now().to_msg()
        for publisher, renderer, frame_id in wanted:
            picture = renderer.render(scene)
            image = Image()
            image.header.stamp = stamp
            image.header.frame_id = frame_id
            image.height, image.width = picture.shape[:2]
            image.encoding = "rgb8"
            image.is_bigendian = 0
            image.step = picture.shape[1] * 3
            image.data = array.array("B", picture.tobytes())
            publisher.publish(image)


def _hold_a_stop_to_its_bound(
    stop: threading.Event, failures: list, ended: threading.Event
) -> None:
    """Wait for the stop, then give the node `STOP_BOUND_S` to end. A thread of its own runs this.

    What ends a node is ROS's to do, and its calls take no bound: one that
    does not return would keep the process until launch or Docker kills it.
    So from the moment a stop is asked for, two things watch.

    This thread. When the bound runs out it says one sentence and ends the
    process with exit code 0: it was asked to stop, and it stops. It writes
    the sentence to stderr itself, because ROS's logger may be what hangs.

    And Python's faulthandler, a second later. A ROS call that hangs keeps
    Python's lock, and then no Python thread runs again, this one included.
    The faulthandler's own thread needs no lock: it writes where every
    thread stands, which names the call, and ends the process with exit
    code 1.

    A stop that ends in time sets `ended`, and neither says anything.
    """
    stop.wait()
    if failures:
        return  # no stop that was asked for: the failure ends the node, in its own words
    faulthandler.dump_traceback_later(STOP_LAST_RESORT_S, exit=True, file=2)
    if ended.wait(STOP_BOUND_S):
        faulthandler.cancel_dump_traceback_later()
        return
    words = f"The cameras did not finish stopping within {STOP_BOUND_S:g} s and end without it."
    os.write(2, f"{words}\n".encode())
    os._exit(0)


def run(stop: threading.Event, args=None) -> int:
    """Run the node until `stop` is set. `start.py` sets it on SIGINT and SIGTERM.

    This process takes the signals itself. Left to ROS, a signal closes the
    context at once, under a timer that is about to publish into it.
    """
    if stop.is_set():
        return 0  # asked to stop during the imports: there is nothing to undo
    failures = []
    ended = threading.Event()
    threading.Thread(
        target=_hold_a_stop_to_its_bound, args=(stop, failures, ended), name="stop", daemon=True
    ).start()
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = CamerasNode()
    if stop.is_set():
        # Asked to stop while it was still starting, by a person or by a
        # launch whose other node gave up. Nothing is wrong: leave quietly.
        node.destroy_node()
        rclpy.try_shutdown()
        ended.set()
        return 0

    # One thread, as in the simulator's node: no callback here waits for
    # another one, and a picture is drawn in four milliseconds.
    executor = SingleThreadedExecutor()
    executor.add_node(node)

    def spin() -> None:
        try:
            executor.spin()
        except Exception as failure:
            failures.append(failure)
        finally:
            stop.set()

    spinning = threading.Thread(target=spin, name="ros", daemon=True)
    spinning.start()
    stop.wait()

    # In this order: let the running callbacks finish, then take ROS away.
    executor.shutdown()
    spinning.join(timeout=5.0)
    node.destroy_node()
    rclpy.try_shutdown()
    ended.set()
    if failures:
        raise failures[0]
    return 0

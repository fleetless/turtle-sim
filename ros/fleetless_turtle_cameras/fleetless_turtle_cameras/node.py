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
"""

import array
import math
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
    """A path's poses as rows of (x, y). A point that is no number is left out."""
    points = np.array([(p.pose.position.x, p.pose.position.y) for p in path.poses], float)
    points = points.reshape(-1, 2)
    return points[np.isfinite(points).all(axis=1)]


class CamerasNode(Node):
    def __init__(self):
        super().__init__(NODE_NAME)
        # What was last heard. A picture is drawn once the first two are known.
        self._kinds = None
        self._pose = None
        self._plan = np.empty((0, 2))
        self._trail = np.empty((0, 2))
        self._warned_about_the_world = False

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

    def _on_odometry(self, odometry: Odometry) -> None:
        pose = odometry.pose.pose
        heading = 2.0 * math.atan2(pose.orientation.z, pose.orientation.w)
        if all(math.isfinite(value) for value in (pose.position.x, pose.position.y, heading)):
            self._pose = (pose.position.x, pose.position.y, heading)

    def _on_world(self, world: World) -> None:
        if world.width == 0 or len(world.kinds) != world.width * world.height:
            if not self._warned_about_the_world:
                self._warned_about_the_world = True
                self.get_logger().warning(
                    f"A /turtle/world of {world.width} x {world.height} cells came with "
                    f"{len(world.kinds)} of them. It is not drawn. This is logged once."
                )
            return
        # A copy of its own: the renderers keep what they drew from it.
        self._kinds = np.array(world.kinds, np.uint8).reshape(world.height, world.width)

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


def run(stop: threading.Event, args=None) -> int:
    """Run the node until `stop` is set. `start.py` sets it on SIGINT and SIGTERM.

    This process takes the signals itself. Left to ROS, a signal closes the
    context at once, under a timer that is about to publish into it.
    """
    if stop.is_set():
        return 0  # asked to stop during the imports: there is nothing to undo
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = CamerasNode()
    if stop.is_set():
        # Asked to stop while it was still starting, by a person or by a
        # launch whose other node gave up. Nothing is wrong: leave quietly.
        node.destroy_node()
        rclpy.try_shutdown()
        return 0

    # One thread, as in the simulator's node: no callback here waits for
    # another one, and a picture is drawn in four milliseconds.
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    failures = []

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
    if failures:
        raise failures[0]
    return 0

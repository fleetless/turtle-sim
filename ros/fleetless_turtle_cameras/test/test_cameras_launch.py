# SPDX-License-Identifier: Apache-2.0
"""The cameras node on a real ROS graph, beside the real simulator.

Both pictures with their size, their encoding and roughly their rate, what
they show, and the two paths: nobody publishes `/turtle/plan` or
`/turtle/trail` when this starts, then this test does, then it takes them
back. The node is right in all three states.
"""

import itertools
import time
import unittest
from pathlib import Path as FilePath

import launch
import launch_ros.actions
import launch_testing.actions
import launch_testing.asserts
import numpy as np
import pytest
import rclpy
from fleetless_turtle_msgs.msg import World
from geometry_msgs.msg import PoseStamped, Twist
from nav_msgs.msg import Odometry, Path
from probe import Probe
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

from fleetless_turtle_cameras.render import constants as look

GARDEN = FilePath(__file__).resolve().parents[3] / "maps" / "garden.png"

TOP = "/turtle/camera/top/image_raw"
EYE = "/turtle/camera/eye/image_raw"

LATCHED = QoSProfile(
    depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE
)

# The turtle starts on the pad, at 0.85 m, 0.85 m, and looks east.
START_M = 0.85
PX_PER_M = look.TOP_SIZE_PX / look.TOP_WINDOW_M
MIDDLE = look.TOP_SIZE_PX // 2


def picture_of(image: Image) -> np.ndarray:
    return np.frombuffer(image.data, np.uint8).reshape(image.height, image.width, 3)


def has(picture: np.ndarray, colour) -> np.ndarray:
    return (picture == colour).all(axis=2)


def path_through(*points: tuple[float, float]) -> Path:
    path = Path()
    path.header.frame_id = "map"
    for x, y in points:
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.pose.position.x, pose.pose.position.y = x, y
        pose.pose.orientation.w = 1.0
        path.poses.append(pose)
    return path


@pytest.mark.launch_test
def generate_test_description():
    simulator = launch_ros.actions.Node(
        package="fleetless_turtle_sim",
        executable="simulator",
        parameters=[{"map": str(GARDEN)}],
        output="screen",
    )
    cameras = launch_ros.actions.Node(
        package="fleetless_turtle_cameras", executable="cameras", output="screen"
    )
    return launch.LaunchDescription([simulator, cameras, launch_testing.actions.ReadyToTest()])


class TestCamerasNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.probe = Probe()
        cls.top = cls.probe.listen(Image, TOP)
        cls.eye = cls.probe.listen(Image, EYE)
        cls.odometry = cls.probe.listen(Odometry, "/odom")
        # The node is up once its pictures flow.
        Probe.until(cls.top, lambda _: True, timeout=60.0, what=TOP)
        Probe.until(cls.eye, lambda _: True, timeout=60.0, what=EYE)

    @classmethod
    def tearDownClass(cls):
        cls.probe.close()
        rclpy.shutdown()

    def next_top(self, where=lambda _: True, timeout=10.0) -> np.ndarray:
        """The next `top` picture that satisfies `where`."""
        return picture_of(
            Probe.until(self.top, lambda image: where(picture_of(image)), timeout, what=TOP)
        )

    def next_eye(self, where=lambda _: True, timeout=10.0) -> np.ndarray:
        return picture_of(
            Probe.until(self.eye, lambda image: where(picture_of(image)), timeout, what=EYE)
        )

    def latched(self, msg_type, topic: str):
        """A latched publisher that the cameras node has found."""
        publisher = self.probe.node.create_publisher(msg_type, topic, LATCHED)
        deadline = time.monotonic() + 10.0
        while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(publisher.get_subscription_count(), 0, topic)
        return publisher

    # The tests run in the order of their names; the last one drives away.

    def test_1_the_node_publishes_two_pictures_and_reads_four_topics(self):
        node = self.probe.node
        published = {TOP: "sensor_msgs/msg/Image", EYE: "sensor_msgs/msg/Image"}
        subscribed = {
            "/odom": "nav_msgs/msg/Odometry",
            "/turtle/world": "fleetless_turtle_msgs/msg/World",
            "/turtle/plan": "nav_msgs/msg/Path",
            "/turtle/trail": "nav_msgs/msg/Path",
        }

        def missing_from_the_graph() -> dict:
            # The graph learns of an endpoint and of the node behind it from
            # two messages, in either order. Both belong to "the graph is there".
            missing = {}
            for topic, type_name in published.items():
                found = [
                    (info.node_name, info.topic_type)
                    for info in node.get_publishers_info_by_topic(topic)
                ]
                if ("turtle_cameras", type_name) not in found:
                    missing[f"the publisher of {topic}"] = found
            for topic, type_name in subscribed.items():
                found = [
                    (info.node_name, info.topic_type)
                    for info in node.get_subscriptions_info_by_topic(topic)
                ]
                if ("turtle_cameras", type_name) not in found:
                    missing[f"the subscriber of {topic}"] = found
            return missing

        deadline = time.monotonic() + 15.0
        while (missing := missing_from_the_graph()) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual(missing, {})
        # And nothing else: it has no say in what the turtle does.
        own = dict(node.get_publisher_names_and_types_by_node("turtle_cameras", "/"))
        self.assertEqual(set(own) - {"/rosout", "/parameter_events"}, {TOP, EYE})

    def test_2_each_picture_has_its_size_and_is_rgb8(self):
        for heard, width, height, frame in (
            (self.top, 480, 480, "map"),
            (self.eye, 320, 240, "base_link"),
        ):
            image = Probe.until(heard, lambda _: True, what="a picture")
            self.assertEqual((image.width, image.height), (width, height))
            self.assertEqual(image.encoding, "rgb8")
            self.assertEqual(image.step, width * 3)
            self.assertEqual(len(image.data), width * height * 3)
            self.assertEqual(image.is_bigendian, 0)
            self.assertEqual(image.header.frame_id, frame)
            # ROS system time, not a clock of the node's own.
            stamp = image.header.stamp.sec + image.header.stamp.nanosec / 1e9
            self.assertLess(abs(stamp - time.time()), 5.0)
        self.assertEqual((look.TOP_SIZE_PX, look.TOP_SIZE_PX), (480, 480))
        self.assertEqual((look.EYE_WIDTH_PX, look.EYE_HEIGHT_PX), (320, 240))

    def test_3_the_pictures_show_the_garden_from_the_pad(self):
        top = self.next_top()
        # The turtle in the middle, on the pad with its mark, the garden wall
        # to the west and south of it, and past the wall, nothing.
        self.assertTrue(
            has(top[MIDDLE - 1 : MIDDLE + 1, MIDDLE - 1 : MIDDLE + 1], look.TURTLE_SHELL_TOP).all()
        )
        self.assertGreater(has(top, look.PAD_MARK).sum(), 20)
        wall = MIDDLE - round((START_M - 0.1) * PX_PER_M)  # the middle of the wall's 0.2 m
        self.assertTrue(has(top[MIDDLE - 40 : MIDDLE - 20, wall - 1 : wall + 1], look.WALL).all())
        self.assertTrue(has(top[MIDDLE + 60 :, : MIDDLE - 60], look.OUTSIDE).all())

        eye = self.next_eye()
        # The pad under it, the sky over it.
        pad = np.abs(eye[-1].astype(int) - np.array(look.PAD)).max(axis=1) < 25
        self.assertTrue(pad.all())
        sky = np.abs(eye[0].astype(int) - np.array(look.SKY)).max(axis=1) < 25
        self.assertTrue(sky.all())

    def test_4_a_path_and_a_trail_are_drawn_when_somebody_publishes_them(self):
        # As launched, nobody does: the simulator of this test has no goals.
        for topic in ("/turtle/plan", "/turtle/trail"):
            self.assertEqual(self.probe.node.count_publishers(topic), 0, topic)
        self.assertFalse(has(self.next_top(), look.PLAN).any())

        plan = self.latched(Path, "/turtle/plan")
        trail = self.latched(Path, "/turtle/trail")
        plan.publish(path_through((START_M, START_M), (3.0, START_M), (3.0, 3.0)))
        drawn = self.next_top(lambda picture: has(picture, look.PLAN).any())
        # Along both legs, and the target's ring around the last point.
        self.assertTrue(has(drawn, look.PLAN)[MIDDLE, MIDDLE + round(1.5 * PX_PER_M)])
        column = MIDDLE + round((3.0 - START_M) * PX_PER_M)
        self.assertTrue(has(drawn, look.PLAN)[MIDDLE - round(1.0 * PX_PER_M), column - 1])
        ring = round(look.TOP_TARGET_RADIUS_M * PX_PER_M)
        target_row = MIDDLE - round((3.0 - START_M) * PX_PER_M)
        self.assertTrue(has(drawn, look.PLAN)[target_row, column + ring - 1])

        trail.publish(path_through((START_M, 2.0), (4.0, 2.0)))
        row = MIDDLE - round((2.0 - START_M) * PX_PER_M)
        on_the_trail = (row, MIDDLE + round(2.0 * PX_PER_M))

        def trail_drawn(picture) -> bool:
            # One pixel wide and between two pixels: it tints the lawn.
            return picture[on_the_trail].astype(int)[0] > picture[row - 5, on_the_trail[1]][0] + 25

        self.next_top(trail_drawn)

        # An empty path takes them away again: no goal, the trail switched off.
        plan.publish(Path())
        trail.publish(Path())
        self.next_top(
            lambda picture: not has(picture, look.PLAN).any() and not trail_drawn(picture)
        )
        self.probe.node.destroy_publisher(plan)
        self.probe.node.destroy_publisher(trail)

    def test_5_a_changed_world_is_a_changed_picture(self):
        heard = self.probe.listen(World, "/turtle/world", qos=LATCHED)
        garden = Probe.until(heard, lambda _: True, what="/turtle/world")
        before = self.next_eye()

        # A rock bed across the turtle's way, one metre ahead: cells 18 and 19 of every row.
        kinds = np.array(garden.kinds, np.uint8).reshape(garden.height, garden.width).copy()
        kinds[2:20, 18:20] = World.ROCK
        changed = World()
        changed.header.frame_id = "map"
        changed.width, changed.height, changed.resolution = garden.width, garden.height, 0.1
        changed.kinds = kinds.tobytes()
        world = self.latched(World, "/turtle/world")
        world.publish(changed)

        horizon, middle = look.EYE_HEIGHT_PX // 2, look.EYE_WIDTH_PX // 2

        def rock_ahead(picture) -> bool:
            return bool(np.abs(picture[horizon, middle].astype(int) - look.ROCK).max() < 30)

        self.assertFalse(rock_ahead(before))
        self.next_eye(rock_ahead)
        column = MIDDLE + round((1.9 - START_M) * PX_PER_M)
        self.next_top(
            lambda picture: np.abs(picture[MIDDLE, column].astype(int) - look.ROCK).max() < 30
        )

        # The garden as it was, for the test after this one.
        world.publish(garden)
        self.next_eye(lambda picture: not rock_ahead(picture))
        self.probe.node.destroy_publisher(world)

    def test_6_ten_pictures_a_second_each_while_the_turtle_drives(self):
        """Six seconds of driving with both cameras subscribed.

        The pictures keep their rate, and the simulator beside them keeps
        its own: drawing is another process's work.
        """
        publisher = self.probe.node.create_publisher(Twist, "/cmd_vel", 10)
        deadline = time.monotonic() + 10.0
        while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(publisher.get_subscription_count(), 0)

        first_eye = self.next_eye()
        top = self.probe.listen(Image, TOP)
        eye = self.probe.listen(Image, EYE)
        odometry = self.probe.listen(Odometry, "/odom")
        forward = Twist()
        forward.linear.x = 0.3
        for _ in range(120):  # six seconds at 20 Hz
            publisher.publish(forward)
            time.sleep(0.05)

        rates = {TOP: Probe.rate(top), EYE: Probe.rate(eye), "/odom": Probe.rate(odometry)}
        gaps = {
            topic: max(later[0] - earlier[0] for earlier, later in itertools.pairwise(heard))
            for topic, heard in ((TOP, top), (EYE, eye))
        }
        measured = (
            ", ".join(f"{topic} {rate:.2f} Hz" for topic, rate in rates.items())
            + "; longest wait for a picture: "
            + ", ".join(f"{topic} {gap * 1000:.0f} ms" for topic, gap in gaps.items())
        )
        print(f"test_6: {measured}")
        for topic in (TOP, EYE):
            self.assertAlmostEqual(rates[topic], look.FRAME_RATE_HZ, delta=2.5, msg=measured)
            self.assertLess(gaps[topic], 0.5, measured)
        self.assertAlmostEqual(rates["/odom"], 20.0, delta=5.0, msg=measured)

        # It drove: the eye sees something else, and the pad's mark has
        # moved to the west of a turtle that is still in the middle.
        moved = self.latest(odometry).pose.pose.position.x - START_M
        self.assertGreater(moved, 1.2, measured)
        self.assertFalse(np.array_equal(self.next_eye(), first_eye))
        now = self.next_top()
        # Wherever it stopped, between two pixels or on one: one of the
        # four in the middle is the top of its shell.
        middle = now[MIDDLE - 1 : MIDDLE + 1, MIDDLE - 1 : MIDDLE + 1]
        self.assertTrue(has(middle, look.TURTLE_SHELL_TOP).any(), middle)
        _, columns = np.nonzero(has(now, look.PAD_MARK))
        self.assertLess(columns.mean(), MIDDLE - 1.0 * PX_PER_M)

    @staticmethod
    def latest(heard: list):
        return heard[-1][1]


@launch_testing.post_shutdown_test()
class TestTheNodesEnd(unittest.TestCase):
    def test_both_nodes_end_cleanly_when_they_are_stopped(self, proc_info, proc_output):
        launch_testing.asserts.assertExitCodes(proc_info)
        said = "".join(output.text.decode() for output in proc_output)
        self.assertNotIn("Traceback", said)
        self.assertIn("The cameras draw top (480 x 480) and eye (320 x 240)", said)

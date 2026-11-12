# SPDX-License-Identifier: Apache-2.0
"""The simulator node on a real ROS graph, without a bridge.

Every topic with its type and roughly its rate, the start state on the
shipped map, and `/cmd_vel`: it moves the turtle, and the turtle stands
again once the commands stop.
"""

import math
import time
import unittest
from pathlib import Path

import launch
import launch_ros.actions
import launch_testing.actions
import pytest
import rclpy
from fleetless_turtle_msgs.msg import TurtleStatus, World
from geometry_msgs.msg import Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from probe import Probe
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, LaserScan, Range
from tf2_msgs.msg import TFMessage

GARDEN = Path(__file__).resolve().parents[3] / "maps" / "garden.png"

LATCHED = QoSProfile(
    depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE
)

# Topic, type, and the rate the spec gives it (None: latched, on change).
PUBLISHED = {
    "/odom": (Odometry, 20.0),
    "/tf": (TFMessage, 20.0),
    "/tf_static": (TFMessage, None),
    "/scan": (LaserScan, 5.0),
    "/range/front": (Range, 10.0),
    "/range/left": (Range, 10.0),
    "/range/right": (Range, 10.0),
    "/battery_state": (BatteryState, 1.0),
    "/turtle/status": (TurtleStatus, 10.0),
    "/turtle/world": (World, None),
    "/map": (OccupancyGrid, None),
}


def type_name(msg_type) -> str:
    package, _, name = msg_type.__module__.split(".")[:3]
    return f"{package}/msg/{msg_type.__name__}"


@pytest.mark.launch_test
def generate_test_description():
    simulator = launch_ros.actions.Node(
        package="fleetless_turtle_sim",
        executable="simulator",
        parameters=[{"map": str(GARDEN)}],
        output="screen",
    )
    return launch.LaunchDescription([simulator, launch_testing.actions.ReadyToTest()])


class TestSimulatorNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.probe = Probe()
        cls.status = cls.probe.listen(TurtleStatus, "/turtle/status")
        cls.odometry = cls.probe.listen(Odometry, "/odom")
        # The node is up once its status flows.
        Probe.until(cls.status, lambda _: True, timeout=30.0, what="/turtle/status")

    @classmethod
    def tearDownClass(cls):
        cls.probe.close()
        rclpy.shutdown()

    def latest_status(self) -> TurtleStatus:
        return Probe.until(self.status, lambda _: True, what="/turtle/status")

    def latest_odometry(self) -> Odometry:
        return Probe.until(self.odometry, lambda _: True, what="/odom")

    # The tests run in the order of their names; the last one drives away.

    def test_1_every_topic_exists_with_its_type(self):
        deadline = time.monotonic() + 15.0
        expected = {topic: [type_name(msg_type)] for topic, (msg_type, _) in PUBLISHED.items()}
        expected["/cmd_vel"] = ["geometry_msgs/msg/Twist"]
        while True:
            graph = dict(self.probe.node.get_topic_names_and_types())
            missing = {
                topic: types for topic, types in expected.items() if graph.get(topic) != types
            }
            if not missing or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        self.assertEqual(missing, {})
        for topic in PUBLISHED:
            publishers = self.probe.node.get_publishers_info_by_topic(topic)
            self.assertIn("turtle_simulator", [info.node_name for info in publishers], topic)
        listeners = self.probe.node.get_subscriptions_info_by_topic("/cmd_vel")
        self.assertIn("turtle_simulator", [info.node_name for info in listeners])

    def test_2_every_topic_has_roughly_its_rate(self):
        heard = {
            topic: self.probe.listen(msg_type, topic)
            for topic, (msg_type, hz) in PUBLISHED.items()
            if hz is not None
        }
        time.sleep(6.0)
        for topic, (_, hz) in PUBLISHED.items():
            if hz is None:
                continue
            measured = Probe.rate(heard[topic])
            self.assertAlmostEqual(measured, hz, delta=hz * 0.25, msg=f"{topic}: {measured:.2f} Hz")

    def test_3_the_latched_topics_reach_a_late_subscriber(self):
        world = self.probe.first(World, "/turtle/world", qos=LATCHED)
        self.assertEqual((world.width, world.height), (256, 256))
        self.assertAlmostEqual(world.resolution, 0.1, places=6)
        self.assertEqual(world.header.frame_id, "map")
        self.assertEqual(len(world.kinds), 256 * 256)
        # Row-major from cell (0, 0): the wall, the pad, the lawn.
        self.assertEqual(world.kinds[0], World.WALL)
        self.assertEqual(world.kinds[8 * 256 + 8], World.PAD)
        self.assertEqual(world.kinds[24 * 256 + 40], World.FREE)
        self.assertEqual(world.kinds[72 * 256 + 200], World.WATER)
        self.assertEqual(world.kinds[50 * 256 + 58], World.ROCK)

        grid = self.probe.first(OccupancyGrid, "/map", qos=LATCHED)
        self.assertEqual((grid.info.width, grid.info.height), (256, 256))
        self.assertAlmostEqual(grid.info.resolution, 0.1, places=6)
        self.assertEqual(grid.header.frame_id, "map")
        self.assertEqual(set(grid.data), {0, 100})
        for index, kind in enumerate(world.kinds):
            blocks = kind in (World.WALL, World.ROCK, World.WATER)
            if (grid.data[index] == 100) != blocks:
                self.fail(f"/map and /turtle/world disagree at index {index}")

        static = self.probe.first(TFMessage, "/tf_static", qos=LATCHED)
        frames = {(t.header.frame_id, t.child_frame_id): t.transform for t in static.transforms}
        self.assertEqual(
            set(frames),
            {
                ("map", "odom"),
                ("base_link", "range_front"),
                ("base_link", "range_left"),
                ("base_link", "range_right"),
            },
        )
        identity = frames[("map", "odom")]
        self.assertEqual((identity.translation.x, identity.translation.y), (0.0, 0.0))
        self.assertEqual((identity.rotation.z, identity.rotation.w), (0.0, 1.0))
        left = frames[("base_link", "range_left")].rotation
        self.assertAlmostEqual(2 * math.atan2(left.z, left.w), math.pi / 2)

    def test_4_the_turtle_starts_on_the_pad(self):
        status = self.latest_status()
        self.assertEqual((status.cell_x, status.cell_y, status.square), (8, 8, "A1"))
        self.assertEqual(status.header.frame_id, "map")
        self.assertEqual(status.mode, "idle")
        self.assertTrue(status.on_charger)
        self.assertFalse(status.crawling or status.bumper or status.trail)
        self.assertEqual((status.speed, status.collisions, status.odometer_m), (0.0, 0, 0.0))

        odometry = self.latest_odometry()
        self.assertEqual((odometry.header.frame_id, odometry.child_frame_id), ("odom", "base_link"))
        self.assertAlmostEqual(odometry.pose.pose.position.x, 0.85)
        self.assertAlmostEqual(odometry.pose.pose.position.y, 0.85)
        self.assertEqual(odometry.pose.pose.orientation.w, 1.0)
        # ROS system time, not a clock of the simulator's own.
        stamp = odometry.header.stamp.sec + odometry.header.stamp.nanosec / 1e9
        self.assertLess(abs(stamp - time.time()), 5.0)

        tf = self.probe.first(TFMessage, "/tf")
        self.assertEqual(
            [(t.header.frame_id, t.child_frame_id) for t in tf.transforms], [("odom", "base_link")]
        )

        battery = self.probe.first(BatteryState, "/battery_state")
        self.assertEqual(battery.percentage, 1.0)
        self.assertAlmostEqual(battery.voltage, 12.6, places=5)
        self.assertTrue(battery.present)
        self.assertEqual(battery.power_supply_status, BatteryState.POWER_SUPPLY_STATUS_FULL)

        scan = self.probe.first(LaserScan, "/scan")
        self.assertEqual(len(scan.ranges), 72)
        self.assertAlmostEqual(scan.angle_min, -math.pi, places=5)
        self.assertAlmostEqual(scan.angle_increment, math.radians(5), places=6)
        self.assertAlmostEqual(scan.range_min, 0.1, places=6)
        self.assertAlmostEqual(scan.range_max, 4.0, places=6)
        # West and south the garden wall is 0.65 m away; east there is lawn.
        self.assertAlmostEqual(scan.ranges[0], 0.65, places=5)
        self.assertAlmostEqual(scan.ranges[18], 0.65, places=5)
        self.assertEqual(scan.ranges[36], math.inf)

        front = self.probe.first(Range, "/range/front")
        right = self.probe.first(Range, "/range/right")
        self.assertEqual(front.range, front.max_range)  # nothing ahead: the maximum, a number
        self.assertAlmostEqual(front.max_range, 4.0, places=6)
        self.assertAlmostEqual(right.range, 0.65, places=5)
        self.assertEqual(right.header.frame_id, "range_right")

    def test_5_cmd_vel_moves_the_turtle_and_it_stands_after_the_last_message(self):
        publisher = self.probe.node.create_publisher(Twist, "/cmd_vel", 10)
        deadline = time.monotonic() + 10.0
        while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(publisher.get_subscription_count(), 0)

        start = self.latest_odometry().pose.pose.position.x
        forward = Twist()
        forward.linear.x = 0.3
        # Fields two wheels cannot follow are ignored.
        forward.linear.y = 5.0
        forward.angular.x = 5.0
        for _ in range(40):  # two seconds at 20 Hz
            publisher.publish(forward)
            time.sleep(0.05)
        last_message = time.monotonic()

        moving = self.latest_status()
        self.assertEqual(moving.mode, "teleop")
        self.assertAlmostEqual(moving.speed, 0.3, places=5)
        self.assertEqual(moving.turn_rate, 0.0)

        # A command is good for half a second: 0.25 s after the last one the
        # turtle still drives, and well within the next second it stands.
        time.sleep(max(0.0, last_message + 0.25 - time.monotonic()))
        self.assertGreater(self.latest_status().speed, 0.0)
        standing = Probe.until(
            self.status, lambda status: status.speed == 0.0, timeout=2.0, what="/turtle/status"
        )
        self.assertLess(time.monotonic() - last_message, 1.5)
        self.assertEqual(standing.speed, 0.0)

        time.sleep(0.3)
        rest = self.latest_odometry().pose.pose.position.x
        self.assertGreater(rest - start, 0.5)
        self.assertLess(rest - start, 0.9)
        self.assertAlmostEqual(self.latest_odometry().pose.pose.position.y, 0.85)
        time.sleep(0.5)
        self.assertEqual(self.latest_odometry().pose.pose.position.x, rest)
        self.assertEqual(self.latest_status().mode, "idle")
        self.assertGreater(self.latest_status().odometer_m, 0.5)

    def test_6_a_value_that_is_no_number_counts_as_zero_and_is_logged_once(self, proc_output):
        publisher = self.probe.node.create_publisher(Twist, "/cmd_vel", 10)
        deadline = time.monotonic() + 10.0
        while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        before = self.latest_odometry().pose.pose.position.x
        broken = Twist()
        broken.linear.x = math.nan
        for _ in range(20):
            publisher.publish(broken)
            time.sleep(0.05)
        proc_output.assertWaitFor("held a value that is not a number", timeout=5.0)
        self.assertEqual(self.latest_odometry().pose.pose.position.x, before)
        self.assertEqual(self.latest_status().mode, "idle")

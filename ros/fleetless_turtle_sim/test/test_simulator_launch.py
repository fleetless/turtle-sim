# SPDX-License-Identifier: Apache-2.0
"""The simulator node on a real ROS graph, without a bridge.

Every topic with its type and roughly its rate, the start state on the
shipped map, and `/cmd_vel`: it moves the turtle, and the turtle stands
again once the commands stop.
"""

import itertools
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
from sensor_msgs.msg import BatteryState, JointState, LaserScan, Range
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
    "/joint_states": (JointState, 20.0),
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
        expected = {topic: [type_name(msg_type)] for topic, (msg_type, _) in PUBLISHED.items()}
        expected["/cmd_vel"] = ["geometry_msgs/msg/Twist"]
        node = self.probe.node

        def missing_from_the_graph() -> dict:
            graph = dict(node.get_topic_names_and_types())
            missing = {
                topic: types for topic, types in expected.items() if graph.get(topic) != types
            }
            # The graph learns of an endpoint and of the node behind it from
            # two messages, in either order. Until both have come, the node
            # is "_NODE_NAME_UNKNOWN_". Both belong to "the graph is there".
            for topic in PUBLISHED:
                names = [info.node_name for info in node.get_publishers_info_by_topic(topic)]
                if "turtle_simulator" not in names:
                    missing[f"the publisher of {topic}"] = names
            names = [info.node_name for info in node.get_subscriptions_info_by_topic("/cmd_vel")]
            if "turtle_simulator" not in names:
                missing["the subscriber of /cmd_vel"] = names
            return missing

        deadline = time.monotonic() + 15.0
        while (missing := missing_from_the_graph()) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual(missing, {})

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
        """Six seconds of commands, judged by the odometry's own stamps.

        Not by this test's clock: when a message was published or arrived
        says little about when the turtle acted on it. Bounds: the pace
        within 3 % and at most one gap over 90 ms between two stamps; one
        thread keeps the pace within 0.5 % and leaves no gap over 56 ms,
        rclpy's multi-threaded executor left three and more such gaps.
        """
        publisher = self.probe.node.create_publisher(Twist, "/cmd_vel", 10)
        deadline = time.monotonic() + 10.0
        while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(publisher.get_subscription_count(), 0)

        # A listener of this test's own, with room for every message of the
        # drive: a gap in what it holds is then a gap in what was sent.
        heard = self.probe.listen(Odometry, "/odom", qos=500)
        Probe.until(heard, lambda _: True, what="/odom")

        def stamp(message) -> float:
            return message.header.stamp.sec + message.header.stamp.nanosec / 1e9

        speed = 0.3
        forward = Twist()
        forward.linear.x = speed
        # Fields two wheels cannot follow are ignored.
        forward.linear.y = 5.0
        forward.angular.x = 5.0
        for _ in range(120):  # six seconds at 20 Hz
            publisher.publish(forward)
            time.sleep(0.05)
        last_command = time.time()

        moving = self.latest_status()
        self.assertEqual(moving.mode, "teleop")
        self.assertAlmostEqual(moving.speed, speed, places=5)
        self.assertEqual(moving.turn_rate, 0.0)

        # It stands again, and stays where it stopped.
        Probe.until(heard, lambda odometry: odometry.twist.twist.linear.x == 0.0, timeout=5.0)
        time.sleep(0.5)
        drive = [message for _, message in heard]
        start, rest = drive[0].pose.pose.position.x, drive[-1].pose.pose.position.x
        self.assertEqual(drive[-2].pose.pose.position.x, rest)
        self.assertAlmostEqual(drive[-1].pose.pose.position.y, 0.85)
        self.assertEqual(self.latest_status().mode, "idle")
        self.assertGreater(self.latest_status().odometer_m, 1.5)

        # The stretch at the full commanded speed, by what the node stamped.
        full = [m for m in drive if abs(m.twist.twist.linear.x - speed) < 1e-6]
        self.assertGreater(len(full), 100, "the turtle hardly reached the commanded speed")
        seconds = stamp(full[-1]) - stamp(full[0])
        metres = full[-1].pose.pose.position.x - full[0].pose.pose.position.x
        pace = metres / (speed * seconds)
        gaps = [stamp(b) - stamp(a) for a, b in itertools.pairwise(full)]
        long_gaps = [round(gap * 1000) for gap in gaps if gap > 0.09]
        measured = (
            f"pace {pace:.3f} over {seconds:.1f} s, longest gap between two odometry stamps "
            f"{max(gaps) * 1000:.0f} ms, gaps over 90 ms: {long_gaps}"
        )
        print(f"test_5: {measured}")

        # A command is good for half a second: the turtle keeps its speed
        # that long after the last one, and stands 0.3 s later.
        kept_speed_for = stamp(full[-1]) - last_command
        self.assertGreater(kept_speed_for, 0.3, measured)
        self.assertLess(kept_speed_for, 0.7, measured)
        standing = next(
            m for m in drive if stamp(m) > stamp(full[-1]) and m.twist.twist.linear.x == 0.0
        )
        self.assertLess(stamp(standing) - last_command, 1.1, measured)

        # Real time: as far as the commanded speed takes it in the time the
        # stamps span.
        self.assertAlmostEqual(pace, 1.0, delta=0.03, msg=measured)
        self.assertGreater(rest - start, speed * 5.5, measured)
        # Steadily: twenty stamps a second, none much later than its turn.
        self.assertLessEqual(len(long_gaps), 1, measured)

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
        warning = "held a value that is not a number"
        proc_output.assertWaitFor(warning, timeout=5.0)
        self.assertEqual(self.latest_odometry().pose.pose.position.x, before)
        self.assertEqual(self.latest_status().mode, "idle")

        # Once: twenty more of them, of both kinds, add no second line.
        broken.linear.x = 0.0
        broken.angular.z = math.inf
        for _ in range(20):
            publisher.publish(broken)
            time.sleep(0.05)
        time.sleep(0.5)
        said = "".join(output.text.decode() for output in proc_output)
        self.assertEqual(said.count(warning), 1, said)
        self.assertEqual(self.latest_odometry().pose.pose.position.x, before)

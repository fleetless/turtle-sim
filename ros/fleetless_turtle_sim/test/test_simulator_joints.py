# SPDX-License-Identifier: Apache-2.0
"""`/joint_states` on a real ROS graph: the model's legs follow the wheels
and its head the turn, as the core's own tests say they should."""

import time
import unittest
from pathlib import Path

import launch
import launch_ros.actions
import launch_testing.actions
import pytest
import rclpy
from geometry_msgs.msg import Twist
from probe import Probe
from sensor_msgs.msg import JointState

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core import joints

GARDEN = Path(__file__).resolve().parents[3] / "maps" / "garden.png"


@pytest.mark.launch_test
def generate_test_description():
    simulator = launch_ros.actions.Node(
        package="fleetless_turtle_sim",
        executable="simulator",
        parameters=[{"map": str(GARDEN)}],
        output="screen",
    )
    return launch.LaunchDescription([simulator, launch_testing.actions.ReadyToTest()])


class TestJointStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.probe = Probe()
        cls.heard = cls.probe.listen(JointState, "/joint_states")
        Probe.until(cls.heard, lambda _: True, timeout=30.0, what="/joint_states")
        cls.commands = cls.probe.node.create_publisher(Twist, "/cmd_vel", 10)
        deadline = time.monotonic() + 10.0
        while cls.commands.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.probe.close()
        rclpy.shutdown()

    def drive(self, linear: float, angular: float, seconds: float) -> list[dict[str, float]]:
        """Send a command for a while; every joint state heard meanwhile, by name."""
        twist = Twist()
        twist.linear.x, twist.angular.z = linear, angular
        first = len(self.heard)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.commands.publish(twist)
            time.sleep(0.05)
        return [
            dict(zip(message.name, message.position, strict=True))
            for _, message in self.heard[first:]
        ]

    # The tests run in the order of their names: standing, turning, driving.

    def test_1_a_standing_turtle_reports_five_joints_at_zero(self):
        state = Probe.until(self.heard, lambda _: True, what="/joint_states")
        self.assertEqual(tuple(state.name), joints.NAMES)
        self.assertEqual(list(state.position), [0.0] * 5)
        # Positions only: nothing here measures a speed or a force.
        self.assertEqual((len(state.velocity), len(state.effort)), (0, 0))
        stamp = state.header.stamp.sec + state.header.stamp.nanosec / 1e9
        self.assertLess(abs(stamp - time.time()), 5.0)

    def test_2_turning_on_the_spot_walks_one_side_backwards_and_turns_the_head(self):
        """To the left, at half the turn rate the turtle may have, from where it stood.

        A wheel then rolls 0.08 m a second, the left one backwards. For the
        first quarter of a stride a leg swings one way only, so for half a
        second the signs say which way each side walks.
        """
        states = self.drive(0.0, 1.0, 0.5)
        self.assertGreater(len(states), 5)
        for state in states:
            # Driving forwards from rest swings the front left leg forwards,
            # to a positive angle, and the front right leg back. Here the
            # left side does the opposite: it walks backwards.
            self.assertLessEqual(state[joints.FRONT_LEFT], 0.0)
            self.assertLessEqual(state[joints.FRONT_RIGHT], 0.0)
            self.assertEqual(state[joints.HIND_LEFT], -state[joints.FRONT_LEFT])
            self.assertEqual(state[joints.HIND_RIGHT], -state[joints.FRONT_RIGHT])
            # The two wheels roll as far as each other, in opposite directions.
            self.assertAlmostEqual(state[joints.FRONT_LEFT], state[joints.FRONT_RIGHT], places=9)
            self.assertGreaterEqual(state[joints.HEAD], 0.0)
        self.assertLess(states[-1][joints.FRONT_LEFT], -0.1 * c.LEG_SWING)
        # The head turns into the curve: half the turn rate, half its 30 degrees.
        self.assertAlmostEqual(states[-1][joints.HEAD], c.HEAD_TURN_LIMIT / 2, places=6)

    def test_3_driving_straight_the_legs_swing_and_the_head_looks_ahead(self):
        time.sleep(1.0)  # standing again
        states = self.drive(0.3, 0.0, 3.0)
        self.assertGreater(len(states), 40)
        for state in states:
            self.assertEqual(state[joints.FRONT_LEFT], -state[joints.HIND_LEFT])
            self.assertEqual(state[joints.FRONT_RIGHT], -state[joints.HIND_RIGHT])
            self.assertEqual(state[joints.HEAD], 0.0)
        # 0.9 m is more than three strides: the legs went both ways, all the way.
        for leg in (joints.FRONT_LEFT, joints.FRONT_RIGHT):
            swing = [state[leg] for state in states]
            self.assertGreater(max(swing), 0.9 * c.LEG_SWING)
            self.assertLess(min(swing), -0.9 * c.LEG_SWING)
            self.assertLessEqual(max(abs(angle) for angle in swing), c.LEG_SWING)

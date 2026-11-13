# SPDX-License-Identifier: Apache-2.0
"""The simulator's services and actions on a real ROS graph, on the shipped map.

Each service with a call it accepts and, where it can refuse, one it
refuses. Each action through the reasons a caller meets: `reached`,
`unreachable`, `target_occupied`, `out_of_range`, `canceled`, `preempted`,
`teleop_override`, `teleported`, `reset`, a new plan after `set_block`, and
a dock that ends `docked`. The core's own tests walk every row of the
reason table; these show that the rows arrive. Only `stuck` has no test
here: it takes a turtle held by something the map does not show.
"""

import itertools
import json
import math
import time
import unittest
from pathlib import Path

import launch
import launch_ros.actions
import launch_testing.actions
import pytest
import rclpy
from action_msgs.msg import GoalStatus
from fleetless_turtle_msgs.action import Dock, DriveTo, DriveToSquare
from fleetless_turtle_msgs.msg import TurtleStatus, World
from fleetless_turtle_msgs.srv import SetBlock, Teleport
from geometry_msgs.msg import Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from nav_msgs.msg import Path as PathMsg
from probe import Probe
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState
from std_srvs.srv import SetBool, Trigger

ROOT = Path(__file__).resolve().parents[3]
GARDEN = ROOT / "maps" / "garden.png"
PLACES = {
    name: (cell["x"], cell["y"])
    for name, cell in json.loads((ROOT / "maps" / "garden.places.json").read_text()).items()
}

LATCHED = QoSProfile(
    depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE
)

SUCCEEDED = GoalStatus.STATUS_SUCCEEDED
CANCELED = GoalStatus.STATUS_CANCELED
ABORTED = GoalStatus.STATUS_ABORTED

# A lane of the garden with nothing in it: the lawn along y = 30, from the
# pad's side to the orchard. Eleven metres to drive, cancel and block.
LANE_START = (20, 30)
LANE_END = (130, 30)


def centre(cell) -> tuple[float, float]:
    return (cell[0] + 0.5) * 0.1, (cell[1] + 0.5) * 0.1


def stamp(message) -> float:
    return message.header.stamp.sec + message.header.stamp.nanosec / 1e9


@pytest.mark.launch_test
def generate_test_description():
    simulator = launch_ros.actions.Node(
        package="fleetless_turtle_sim",
        executable="simulator",
        parameters=[{"map": str(GARDEN)}],
        output="screen",
    )
    return launch.LaunchDescription([simulator, launch_testing.actions.ReadyToTest()])


class TestServicesAndActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.probe = Probe()
        cls.status = cls.probe.listen(TurtleStatus, "/turtle/status")
        cls.battery = cls.probe.listen(BatteryState, "/battery_state")
        cls.odometry = cls.probe.listen(Odometry, "/odom", qos=500)
        cls.plan = cls.probe.listen(PathMsg, "/turtle/plan", qos=LATCHED)
        cls.trail = cls.probe.listen(PathMsg, "/turtle/trail", qos=LATCHED)
        cls.world = cls.probe.listen(World, "/turtle/world", qos=LATCHED)
        cls.map = cls.probe.listen(OccupancyGrid, "/map", qos=LATCHED)
        cls.cmd_vel = cls.probe.node.create_publisher(Twist, "/cmd_vel", 10)
        Probe.until(cls.status, lambda _: True, timeout=30.0, what="/turtle/status")

    @classmethod
    def tearDownClass(cls):
        cls.probe.close()
        rclpy.shutdown()

    def setUp(self):
        """Every test starts on the pad, with the map as loaded and no goal."""
        response = self.probe.call(Trigger, "/turtle/reset")
        self.assertTrue(response.success)
        self.until_status(lambda s: (s.cell_x, s.cell_y, s.mode) == (8, 8, "idle") and not s.trail)

    # --- Helpers --------------------------------------------------------------

    def until_status(self, where, timeout=10.0) -> TurtleStatus:
        return Probe.until(self.status, where, timeout, "/turtle/status")

    def next_status(self) -> TurtleStatus:
        return self.until_status(lambda _: True)

    def assert_cell(self, x: int, y: int, kind: int):
        """`/turtle/world` and `/map` both say that the cell is of this kind.

        The node publishes both before its service answers. They still
        travel on their own, so the answer may overtake them by a moment.
        """
        index = y * 256 + x
        occupied = 100 if kind in (World.WALL, World.ROCK, World.WATER) else 0
        Probe.newest(self.world, lambda w: w.kinds[index] == kind, 5.0, "/turtle/world")
        Probe.newest(self.map, lambda m: m.data[index] == occupied, 5.0, "/map")

    def next_odometry(self) -> Odometry:
        return Probe.until(self.odometry, lambda _: True, what="/odom")

    def teleport(self, cell, heading_deg=0.0):
        response = self.probe.call(
            Teleport, "/turtle/teleport", x=cell[0], y=cell[1], heading_deg=heading_deg
        )
        self.assertTrue(response.success, response.message)
        self.until_status(lambda s: (s.cell_x, s.cell_y) == cell)

    def at_full_speed(self, timeout=10.0):
        Probe.until(
            self.odometry, lambda o: o.twist.twist.linear.x > 0.49, timeout, "/odom at full speed"
        )

    def assert_standing(self):
        """The turtle stands now, and a second later it stands where it stood."""
        odometry = self.next_odometry()
        self.assertEqual(
            (odometry.twist.twist.linear.x, odometry.twist.twist.angular.z), (0.0, 0.0)
        )
        time.sleep(1.0)
        later = self.next_odometry()
        self.assertEqual(later.pose.pose.position, odometry.pose.pose.position)

    def on_the_lane(self):
        """A drive along the lane, at full speed by the time this returns."""
        self.teleport(LANE_START)
        sent = self.probe.send(DriveTo, "/turtle/drive_to", x=LANE_END[0], y=LANE_END[1])
        self.at_full_speed()
        return sent

    # --- Services -------------------------------------------------------------
    # `reset` and `set_trail` have no refusal to give: the one always
    # succeeds, the other takes true and false.

    def test_10_reset_puts_everything_back(self):
        self.teleport((60, 30), 90.0)
        self.assert_cell(80, 30, World.FREE)
        placed = self.probe.call(SetBlock, "/turtle/set_block", x=80, y=30, occupied=True)
        self.assertTrue(placed.success)
        self.assert_cell(80, 30, World.ROCK)
        self.assertTrue(self.probe.call(SetBool, "/turtle/set_trail", data=True).success)
        self.until_status(lambda s: s.trail)

        response = self.probe.call(Trigger, "/turtle/reset")
        self.assertTrue(response.success)
        self.assertEqual(
            response.message,
            "The turtle is back on the pad, the battery is full, the map is as loaded.",
        )
        status = self.until_status(lambda s: (s.cell_x, s.cell_y) == (8, 8))
        self.assertEqual((status.heading_deg, status.trail), (0.0, False))
        self.assertEqual((status.collisions, status.odometer_m), (0, 0.0))
        # The map as loaded, on both topics.
        self.assert_cell(80, 30, World.FREE)

    def test_11_teleport_puts_the_turtle_on_a_cell_or_says_why_not(self):
        x, y = PLACES["open"]
        response = self.probe.call(Teleport, "/turtle/teleport", x=x, y=y, heading_deg=90.0)
        self.assertTrue(response.success)
        self.assertEqual(response.message, f"The turtle is on cell ({x}, {y}), heading 90 degrees.")
        status = self.until_status(lambda s: (s.cell_x, s.cell_y) == (x, y))
        self.assertEqual((status.square, status.heading_deg, status.speed), ("C2", 90.0, 0.0))
        odometry = self.next_odometry()
        self.assertAlmostEqual(odometry.pose.pose.position.x, centre((x, y))[0])
        self.assertAlmostEqual(odometry.pose.pose.position.y, centre((x, y))[1])

        wall_x, wall_y = PLACES["wall"]
        for fields, sentence in [
            (
                {"x": wall_x, "y": wall_y},
                f"Cell ({wall_x}, {wall_y}) is a wall. Teleport declined.",
            ),
            (
                {"x": 300, "y": 5},
                "Cell (300, 5) is outside the grid, 0 to 255. Teleport declined.",
            ),
            ({"x": 2, "y": 100}, "The turtle does not fit on cell (2, 100). Teleport declined."),
            (
                {"x": 100, "y": 100, "heading_deg": math.nan},
                "A heading of nan is not an angle. Teleport declined.",
            ),
        ]:
            response = self.probe.call(Teleport, "/turtle/teleport", **fields)
            self.assertFalse(response.success, fields)
            self.assertEqual(response.message, sentence)
        status = self.next_status()
        self.assertEqual((status.cell_x, status.cell_y, status.heading_deg), (x, y, 90.0))

    def test_12_set_block_changes_a_cell_and_both_maps_or_says_why_not(self):
        response = self.probe.call(SetBlock, "/turtle/set_block", x=60, y=30, occupied=True)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Cell (60, 30) is a rock now.")
        self.assert_cell(60, 30, World.ROCK)

        response = self.probe.call(SetBlock, "/turtle/set_block", x=60, y=30, occupied=False)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Cell (60, 30) is free now.")
        self.assert_cell(60, 30, World.FREE)

        # `occupied: false` frees a cell whatever it was: the middle of the
        # pond, too. It is what the message holds when nobody sets it.
        self.assert_cell(200, 72, World.WATER)
        self.assertTrue(self.probe.call(SetBlock, "/turtle/set_block", x=200, y=72).success)
        self.assert_cell(200, 72, World.FREE)

        self.teleport(PLACES["open"])
        time.sleep(0.5)
        published = len(self.world)
        for fields, sentence in [
            ({"x": 5, "y": 5}, "Cell (5, 5) is part of the charging pad. Nothing changed."),
            (
                {"x": PLACES["open"][0] + 1, "y": PLACES["open"][1]},
                "The turtle's footprint touches cell (41, 24). Nothing changed.",
            ),
            ({"x": 256, "y": 0}, "Cell (256, 0) is outside the grid, 0 to 255. Nothing changed."),
            ({"x": -1, "y": -1}, "Cell (-1, -1) is outside the grid, 0 to 255. Nothing changed."),
        ]:
            for occupied in (True, False):
                response = self.probe.call(
                    SetBlock, "/turtle/set_block", occupied=occupied, **fields
                )
                self.assertFalse(response.success, fields)
                self.assertEqual(response.message, sentence)
        time.sleep(0.5)
        self.assertEqual(len(self.world), published)

    def test_13_set_trail_records_a_point_every_tenth_of_a_metre_and_clears_them(self):
        self.teleport(LANE_START)
        response = self.probe.call(SetBool, "/turtle/set_trail", data=True)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "The trail is being recorded.")
        self.assertTrue(self.until_status(lambda s: s.trail).trail)

        forward = Twist()
        forward.linear.x = 0.4
        for _ in range(60):  # three seconds
            self.cmd_vel.publish(forward)
            time.sleep(0.05)
        self.until_status(lambda s: s.speed == 0.0)
        driven = self.next_status().odometer_m
        trail = Probe.newest(
            self.trail, lambda t: len(t.poses) >= int(driven / 0.1), 5.0, "/turtle/trail"
        )
        self.assertEqual(trail.header.frame_id, "map")
        points = [(pose.pose.position.x, pose.pose.position.y) for pose in trail.poses]
        self.assertAlmostEqual(points[0][0], centre(LANE_START)[0])
        self.assertAlmostEqual(points[0][1], centre(LANE_START)[1])
        for a, b in itertools.pairwise(points):
            self.assertAlmostEqual(math.dist(a, b), 0.1, delta=0.011)
        # At most once a second, by the node's own stamps: when a message
        # arrived says little about when it was sent.
        published = [stamp(message) for _, message in self.trail[-4:]]
        self.assertGreater(len(published), 2)
        for earlier, later in itertools.pairwise(published):
            self.assertGreater(later - earlier, 0.99)

        response = self.probe.call(SetBool, "/turtle/set_trail", data=False)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "The trail is off and empty.")
        Probe.newest(self.trail, lambda t: len(t.poses) == 0, 5.0, "/turtle/trail")
        self.assertFalse(self.until_status(lambda s: not s.trail).trail)

    def test_13b_what_a_service_does_to_the_trail_is_published_before_it_answers(self):
        """The trail grows at most once a second. A service does not wait for
        that second: a trail it emptied or switched on is out before its
        answer is, as everything else a service changed."""
        forward = Twist()
        forward.linear.x = 0.4

        def with_a_trail_just_published():
            """Driving with the trail on, a message of it a moment old: the next is a second off."""
            self.teleport(LANE_START)
            self.assertTrue(self.probe.call(SetBool, "/turtle/set_trail", data=True).success)
            for _ in range(2):  # the second of them is growth for certain
                heard = len(self.trail)
                deadline = time.monotonic() + 10.0
                while len(self.trail) == heard and time.monotonic() < deadline:
                    self.cmd_vel.publish(forward)
                    time.sleep(0.05)
                self.assertGreater(len(self.trail), heard)
            self.assertGreater(len(self.trail[-1][1].poses), 2)

        def told_before_the_answer(call, poses: int):
            """Call a service: the trail it leaves is stamped before its answer, and is here."""
            response = call()
            answered, answered_at = time.time(), time.monotonic()
            self.assertTrue(response.success)
            trail = Probe.newest(self.trail, lambda t: len(t.poses) == poses, 5.0, "/turtle/trail")
            self.assertLess(stamp(trail), answered)
            self.assertLess(self.trail[-1][0] - answered_at, 0.5)

        with_a_trail_just_published()
        told_before_the_answer(
            lambda: self.probe.call(SetBool, "/turtle/set_trail", data=False), poses=0
        )
        # Switched on again at once: the trail is one point, where the turtle is.
        told_before_the_answer(
            lambda: self.probe.call(SetBool, "/turtle/set_trail", data=True), poses=1
        )

        with_a_trail_just_published()
        told_before_the_answer(lambda: self.probe.call(Trigger, "/turtle/reset"), poses=0)

    def test_13c_a_service_that_left_the_trail_alone_does_not_send_it_out_early(self):
        """The trail's growth keeps to once a second, whatever is called meanwhile.

        Every service publishes what it changed before it answers. A rock put
        down and taken away far off, five times a second while the turtle
        drives with the trail on, changes a cell and nothing about the trail,
        and the trail gains a point between any two such calls. It still
        comes once a second, not with every answer.
        """
        self.teleport(LANE_START)
        self.assertTrue(self.probe.call(SetBool, "/turtle/set_trail", data=True).success)
        Probe.newest(self.trail, lambda t: len(t.poses) == 1, 5.0, "/turtle/trail")
        switched_on = len(self.trail) - 1  # the one message a service does send at once

        far = PLACES["far"]
        forward = Twist()
        forward.linear.x = 0.5
        calls, ticks, began = 0, 0, time.monotonic()
        while time.monotonic() - began < 6.0:
            self.cmd_vel.publish(forward)
            if ticks % 4 == 0:  # every fifth of a second
                answer = self.probe.call(
                    SetBlock, "/turtle/set_block", x=far[0], y=far[1], occupied=calls % 2 == 0
                )
                self.assertTrue(answer.success, answer.message)
                calls += 1
            ticks += 1
            time.sleep(max(0.0, began + ticks * 0.05 - time.monotonic()))
        # Three calls a second at the least, or this test did not do what it says.
        self.assertGreater(calls, 18)

        heard = [message for _, message in self.trail[switched_on:]]
        # There was growth to send early: about five points a second at this speed.
        self.assertGreater(len(heard[-1].poses), 20)
        # About once a second, by the node's own stamps. Not under 0.99 s: the
        # node waits a full second on its monotonic clock and stamps the
        # message a moment later from the ROS clock, and a hundredth is for
        # the two clocks. Not over 1.5 s: a point is due every fifth of a
        # second, so the trail has grown whenever its second is over, and half
        # a second is for a machine that is busy.
        gaps = [later - earlier for earlier, later in itertools.pairwise(map(stamp, heard))]
        print(f"test_13c: {calls} calls of set_block; the trail's messages, seconds apart: {gaps}")
        self.assertGreaterEqual(len(gaps), 4, gaps)
        for gap in gaps:
            self.assertGreater(gap, 0.99, gaps)
            self.assertLess(gap, 1.5, gaps)

    # --- Actions --------------------------------------------------------------

    def test_20_a_drive_to_a_cell_is_reached_with_feedback_and_a_path(self):
        x, y = PLACES["open"]
        plans = len(self.plan)
        sent = self.probe.send(DriveTo, "/turtle/drive_to", x=x, y=y)
        self.until_status(lambda s: s.mode == "auto")
        status, result = sent.result(timeout=30.0)
        self.assertEqual(status, SUCCEEDED)
        self.assertTrue(result.success)
        self.assertEqual(result.reason, "reached")
        self.assertEqual(result.message, f"The turtle reached cell ({x}, {y}).")
        self.assertEqual((result.cell_x, result.cell_y), (x, y))
        self.assertAlmostEqual(result.distance_m, math.hypot(3.2, 1.6), delta=0.02)
        self.assertGreater(result.duration_s, 7.5)
        self.assertLess(result.duration_s, 10.0)
        self.assert_standing()
        ended = self.next_status()
        self.assertEqual((ended.cell_x, ended.cell_y, ended.mode), (x, y, "idle"))

        # Feedback: about twice a second, the way left counting down.
        feedback = [message for _, message in sent.feedback]
        self.assertEqual(sent.phases(), ["planning", "driving"])
        self.assertGreaterEqual(len(feedback), int(result.duration_s * 2) - 2)
        self.assertLessEqual(len(feedback), int(result.duration_s * 2) + 4)
        remaining = [message.remaining_m for message in feedback]
        self.assertEqual(remaining, sorted(remaining, reverse=True))
        self.assertAlmostEqual(remaining[0], math.hypot(3.2, 1.6), delta=0.01)
        self.assertGreater(feedback[-1].progress, 0.8)
        self.assertEqual((feedback[0].cell_x, feedback[0].cell_y), (8, 8))

        # The path: published when it was planned, and empty again at the end.
        published = [message for _, message in self.plan[plans:]]
        self.assertEqual([len(path.poses) for path in published], [2, 0])
        path = published[0]
        self.assertEqual(path.header.frame_id, "map")
        first, last = path.poses[0].pose, path.poses[-1].pose
        self.assertAlmostEqual(first.position.x, 0.85)
        self.assertAlmostEqual(first.position.y, 0.85)
        self.assertAlmostEqual(last.position.x, centre((x, y))[0])
        self.assertAlmostEqual(last.position.y, centre((x, y))[1])
        # Each pose looks along the path.
        heading = 2 * math.atan2(first.orientation.z, first.orientation.w)
        self.assertAlmostEqual(heading, math.atan2(1.6, 3.2))

    def test_21_a_cell_no_path_leads_to_is_unreachable(self):
        x, y = PLACES["enclosed"]
        status, result = self.probe.send(DriveTo, "/turtle/drive_to", x=x, y=y).result()
        self.assertEqual(status, ABORTED)
        self.assertEqual((result.success, result.reason), (False, "unreachable"))
        self.assertEqual(result.message, f"No path leads from cell (8, 8) to cell ({x}, {y}).")
        self.assertEqual((result.cell_x, result.cell_y, result.distance_m), (8, 8, 0.0))

    def test_22_a_cell_that_blocks_is_occupied(self):
        x, y = PLACES["wall"]
        status, result = self.probe.send(DriveTo, "/turtle/drive_to", x=x, y=y).result()
        self.assertEqual(status, ABORTED)
        self.assertEqual((result.success, result.reason), (False, "target_occupied"))
        self.assertEqual(result.message, f"Cell ({x}, {y}) is a wall.")
        # A free cell beside the garden wall: nothing is on it, and the turtle does not fit.
        status, result = self.probe.send(DriveTo, "/turtle/drive_to", x=2, y=100).result()
        self.assertEqual((status, result.reason), (ABORTED, "target_occupied"))
        self.assertEqual(result.message, "The turtle does not fit on cell (2, 100).")

    def test_23_what_names_no_cell_and_no_square_is_out_of_range(self):
        for action, name, fields, sentence in [
            (
                DriveTo,
                "/turtle/drive_to",
                {"x": 300, "y": 5},
                "Cell (300, 5) is outside the grid, 0 to 255.",
            ),
            (
                DriveToSquare,
                "/turtle/drive_to_square",
                {"square": "Z9"},
                '"Z9" names no square: they run from A1 to P16.',
            ),
        ]:
            status, result = self.probe.send(action, name, **fields).result()
            self.assertEqual(status, ABORTED, fields)
            self.assertEqual((result.success, result.reason), (False, "out_of_range"), fields)
            self.assertEqual(result.message, sentence)
        self.assertEqual(self.next_status().mode, "idle")

    def test_24_a_cancel_is_accepted_and_ends_within_two_seconds_with_the_turtle_standing(self):
        sent = self.on_the_lane()
        asked = time.monotonic()
        answer = sent.cancel()
        self.assertEqual(answer.return_code, 0)  # accepted
        self.assertEqual(len(answer.goals_canceling), 1)
        status, result = sent.result(timeout=5.0)
        took = time.monotonic() - asked
        print(f"test_24: canceled {took:.2f} s after the cancel was asked for")
        self.assertLess(took, 2.0)
        self.assertEqual(status, CANCELED)
        self.assertEqual((result.success, result.reason), (False, "canceled"))
        self.assertEqual(result.message, "The goal was cancelled on the way to cell (130, 30).")
        self.assert_standing()
        status = self.next_status()
        self.assertEqual((status.mode, status.cell_y), ("idle", 30))
        self.assertEqual((result.cell_x, result.cell_y), (status.cell_x, status.cell_y))
        self.assertEqual(len(self.plan[-1][1].poses), 0)

    def test_25_a_new_goal_preempts_the_running_one_on_another_server_and_on_the_same(self):
        first = self.on_the_lane()
        second = self.probe.send(DriveToSquare, "/turtle/drive_to_square", square="B2")
        status, result = first.result(timeout=5.0)
        self.assertEqual(status, ABORTED)
        self.assertEqual((result.success, result.reason), (False, "preempted"))
        self.assertEqual(result.message, "Another goal was accepted on the way to cell (130, 30).")
        status, result = second.result(timeout=30.0)
        self.assertEqual(status, SUCCEEDED)
        self.assertEqual(result.message, "The turtle reached square B2 at cell (23, 23).")
        self.assertEqual(self.next_status().square, "B2")

        third = self.probe.send(DriveTo, "/turtle/drive_to", x=LANE_END[0], y=LANE_END[1])
        self.at_full_speed()
        fourth = self.probe.send(DriveTo, "/turtle/drive_to", x=30, y=23)
        status, result = third.result(timeout=5.0)
        self.assertEqual((status, result.reason), (ABORTED, "preempted"))
        status, result = fourth.result(timeout=30.0)
        self.assertEqual((status, result.reason), (SUCCEEDED, "reached"))

    def test_25b_a_new_goal_overtakes_a_cancel_that_is_still_braking_the_turtle(self):
        sent = self.on_the_lane()
        answer = sent.cancel()
        self.assertEqual(answer.return_code, 0)  # accepted, as every cancel is
        # Half a second of braking lies ahead. The next goal is there sooner.
        following = self.probe.send(DriveToSquare, "/turtle/drive_to_square", square="B2")
        status, result = sent.result(timeout=5.0)
        self.assertEqual(status, ABORTED)
        self.assertEqual((result.success, result.reason), (False, "preempted"))
        self.assertEqual(result.message, "Another goal was accepted on the way to cell (130, 30).")
        status, result = following.result(timeout=30.0)
        self.assertEqual((status, result.reason), (SUCCEEDED, "reached"))

    def test_26_a_zero_twist_ends_nothing_and_a_non_zero_one_ends_the_goal(self):
        sent = self.on_the_lane()
        heard = len(self.odometry)
        for _ in range(30):  # a second and a half of the bridge's failsafe
            self.cmd_vel.publish(Twist())
            time.sleep(0.05)
        self.assertFalse(sent.ended)
        self.assertEqual(self.next_status().mode, "auto")
        speeds = [message.twist.twist.linear.x for _, message in self.odometry[heard:]]
        self.assertGreater(len(speeds), 20)
        self.assertGreater(min(speeds), 0.49)  # it never let go of the wheel

        turn = Twist()
        turn.angular.z = 0.5
        self.cmd_vel.publish(turn)
        status, result = sent.result(timeout=5.0)
        self.assertEqual(status, ABORTED)
        self.assertEqual((result.success, result.reason), (False, "teleop_override"))
        self.assertEqual(
            result.message, "A velocity command took over on the way to cell (130, 30)."
        )
        self.assertEqual(self.next_status().mode, "teleop")
        self.until_status(lambda s: s.mode == "idle" and s.speed == 0.0)

    def test_26b_a_teleport_and_a_reset_end_a_goal_and_a_declined_teleport_does_not(self):
        sent = self.on_the_lane()
        declined = self.probe.call(Teleport, "/turtle/teleport", x=2, y=100)
        self.assertFalse(declined.success)
        time.sleep(0.5)
        self.assertFalse(sent.ended)
        moved = self.probe.call(Teleport, "/turtle/teleport", x=40, y=24, heading_deg=90.0)
        self.assertTrue(moved.success)
        status, result = sent.result(timeout=5.0)
        self.assertEqual(status, ABORTED)
        self.assertEqual((result.success, result.reason), (False, "teleported"))
        self.assertEqual(result.message, "The turtle was teleported on the way to cell (130, 30).")
        self.assertEqual(result.cell_y, 30)  # where the goal ended, not where the turtle went
        self.assert_standing()
        self.assertEqual(self.next_status().cell_x, 40)

        sent = self.probe.send(Dock, "/turtle/dock")
        self.until_status(lambda s: s.mode == "docking" and s.speed > 0.4)
        self.assertTrue(self.probe.call(Trigger, "/turtle/reset").success)
        status, result = sent.result(timeout=5.0)
        self.assertEqual(status, ABORTED)
        self.assertEqual((result.success, result.reason), (False, "reset"))
        self.assertEqual(result.message, "The simulator was reset on the way to the charging pad.")
        status = self.until_status(lambda s: (s.cell_x, s.cell_y) == (8, 8))
        self.assertEqual(status.mode, "idle")
        self.assertEqual(len(self.plan[-1][1].poses), 0)

    def test_27_a_block_on_the_path_makes_the_turtle_plan_again(self):
        plans = len(self.plan)
        sent = self.on_the_lane()
        # A row of rocks across the lane, a few metres ahead. North of it
        # the gap to the hedge is too narrow, so the way round is south.
        for y in range(18, 43):
            response = self.probe.call(SetBlock, "/turtle/set_block", x=70, y=y, occupied=True)
            self.assertTrue(response.success, response.message)
        status, result = sent.result(timeout=60.0)
        self.assertEqual(status, SUCCEEDED)
        self.assertEqual(result.reason, "reached")
        self.assertEqual(sent.phases(), ["planning", "driving", "replanning"])
        self.assertGreater(result.distance_m, 11.0 + 0.2)  # round the rocks
        self.assertEqual(self.next_status().collisions, 0)

        # Two paths were published and then none. The second starts where
        # the turtle stood and keeps clear of the rocks.
        published = [message for _, message in self.plan[plans:]]
        self.assertEqual([len(path.poses) > 0 for path in published], [True, True, False])
        straight, around = published[0], published[1]
        self.assertEqual(len(straight.poses), 2)
        self.assertGreater(len(around.poses), 2)
        self.assertGreater(around.poses[0].pose.position.x, straight.poses[0].pose.position.x)
        self.assertLess(around.poses[0].pose.position.x, 7.0 - 0.15)
        for a, b in itertools.pairwise(around.poses):
            (ax, ay), (bx, by) = ((pose.pose.position.x, pose.pose.position.y) for pose in (a, b))
            for i in range(101):
                x, y = ax + (bx - ax) * i / 100, ay + (by - ay) * i / 100
                # No point of the path lies within the footprint's reach of the rocks.
                self.assertFalse(6.9 < x < 7.2 and 1.7 < y < 4.4, (x, y))

    def test_28_a_dock_ends_docked_on_the_pad_and_the_battery_charges_by_itself(self):
        # From the far end of the lane: twelve metres cost more charge than
        # the last steps on the pad give back.
        self.teleport(LANE_END, 180.0)
        sent = self.probe.send(Dock, "/turtle/dock")
        self.until_status(lambda s: s.mode == "docking")
        status, result = sent.result(timeout=60.0)
        ended = time.monotonic()
        self.assertEqual(status, SUCCEEDED)
        self.assertEqual((result.success, result.reason), (True, "docked"))
        self.assertEqual(result.message, "The turtle stands on the charging pad.")
        # It did not wait for the charge: the result holds what it arrived with.
        self.assertLess(result.battery_percent, 99.0)
        self.assertEqual(sent.phases(), ["planning", "driving"])
        feedback = [message for _, message in sent.feedback]
        self.assertAlmostEqual(
            feedback[0].remaining_m, math.dist(centre(LANE_END), centre((8, 8))), delta=0.01
        )
        self.assertLess(feedback[-1].remaining_m, 0.5)
        self.assertLess(min(message.battery_percent for message in feedback), 99.0)

        # No goal runs now. The pad charges all the same, and the mode says so.
        status = self.until_status(lambda s: s.mode != "docking")
        self.assertEqual(
            (status.mode, status.cell_x, status.cell_y, status.on_charger),
            ("charging", 8, 8, True),
        )
        full = self.until_status(lambda s: s.mode != "charging", timeout=30.0)
        self.assertEqual((full.mode, full.on_charger), ("idle", True))
        Probe.until(self.battery, lambda b: b.percentage == 1.0, 5.0, "/battery_state at 100 %")
        since = [message for arrived, message in self.battery if arrived > ended]
        charge = [message.percentage for message in since]
        self.assertGreaterEqual(charge[0], result.battery_percent / 100.0 - 1e-6)
        self.assertLess(charge[0], 1.0)
        self.assertEqual(charge, sorted(charge))
        self.assertEqual(charge[-1], 1.0)
        self.assertEqual(since[0].power_supply_status, BatteryState.POWER_SUPPLY_STATUS_CHARGING)
        self.assertEqual(since[-1].power_supply_status, BatteryState.POWER_SUPPLY_STATUS_FULL)
        self.assert_standing()

    def test_28b_a_late_subscriber_hears_the_path_and_the_trail_and_a_path_ends_on_its_target(self):
        """What the cameras node counts on. It subscribes to both paths as
        latched topics whenever it starts, draws the last pose of the path
        as the target, and takes an empty path for "no goal"."""

        def late(topic):
            """What a subscriber that joins now is told, without anything new being published."""
            heard = self.probe.listen(PathMsg, topic, qos=LATCHED)
            return Probe.newest(heard, lambda _: True, 5.0, f"{topic}, joined late")

        def last_point(path):
            return path.poses[-1].pose.position.x, path.poses[-1].pose.position.y

        self.assertEqual(len(late("/turtle/plan").poses), 0)  # no goal, no path
        self.assertEqual(len(late("/turtle/trail").poses), 0)  # the trail is off

        # A drive into a square: the path ends on the cell the turtle chose.
        self.teleport(LANE_START)
        self.assertTrue(self.probe.call(SetBool, "/turtle/set_trail", data=True).success)
        published = len(self.plan)
        sent = self.probe.send(DriveToSquare, "/turtle/drive_to_square", square="H2")
        self.at_full_speed()
        time.sleep(1.5)
        self.assertEqual(len(self.plan), published + 1)  # planned once, published once
        plan = late("/turtle/plan")
        self.assertEqual(plan.header.frame_id, "map")
        self.assertEqual(len(plan.poses), 2)
        self.assertAlmostEqual(plan.poses[0].pose.position.x, centre(LANE_START)[0])
        for got, expected in zip(last_point(plan), centre((119, 23)), strict=True):
            self.assertAlmostEqual(got, expected)

        # The trail, once the turtle stands and nothing more is published.
        self.assertEqual(sent.cancel().return_code, 0)
        status, result = sent.result(timeout=5.0)
        self.assertEqual((status, result.reason), (CANCELED, "canceled"))
        self.assertEqual(result.message, "The goal was cancelled on the way to square H2.")
        self.assert_standing()
        time.sleep(1.5)
        published = len(self.trail)
        trail = late("/turtle/trail")
        self.assertEqual(len(self.trail), published)  # told from the latch, not from a new message
        self.assertEqual(trail.header.frame_id, "map")
        self.assertGreater(len(trail.poses), 5)
        self.assertAlmostEqual(trail.poses[0].pose.position.x, centre(LANE_START)[0])
        self.assertAlmostEqual(trail.poses[0].pose.position.y, centre(LANE_START)[1])
        odometry = self.next_odometry()
        self.assertAlmostEqual(last_point(trail)[0], odometry.pose.pose.position.x, delta=0.11)
        self.assertEqual(len(late("/turtle/plan").poses), 0)  # the goal ended: no path

        # A dock: the path ends on the pad's start cell.
        sent = self.probe.send(Dock, "/turtle/dock")
        self.until_status(lambda s: s.mode == "docking" and s.speed > 0.4)
        plan = late("/turtle/plan")
        self.assertGreaterEqual(len(plan.poses), 2)
        for got, expected in zip(last_point(plan), centre((8, 8)), strict=True):
            self.assertAlmostEqual(got, expected)
        self.assertEqual(sent.cancel().return_code, 0)
        self.assertEqual(sent.result(timeout=5.0)[1].reason, "canceled")
        self.assertEqual(len(late("/turtle/plan").poses), 0)

    def test_29_the_turtle_keeps_its_pace_while_a_goal_runs(self):
        """The drive of test_5 in the other launch test, driven by a goal.

        A goal's callback is a coroutine that awaits a future, feedback goes
        out twice a second, the path is checked once a second and the trail
        is published once a second. None of it may hold the step timer up.
        Judged by the odometry's own stamps, with the same bounds: the pace
        within 3 %, at most one gap over 90 ms between two stamps.
        """
        self.teleport(LANE_START)
        self.assertTrue(self.probe.call(SetBool, "/turtle/set_trail", data=True).success)
        heard = len(self.odometry)
        sent = self.probe.send(DriveTo, "/turtle/drive_to", x=LANE_END[0], y=LANE_END[1])
        status, result = sent.result(timeout=60.0)
        self.assertEqual((status, result.reason), (SUCCEEDED, "reached"))

        speed = 0.5
        drive = [message for _, message in self.odometry[heard:]]
        full = [m for m in drive if abs(m.twist.twist.linear.x - speed) < 1e-6]
        self.assertGreater(len(full), 300, "the turtle hardly reached full speed")
        seconds = stamp(full[-1]) - stamp(full[0])
        metres = full[-1].pose.pose.position.x - full[0].pose.pose.position.x
        pace = metres / (speed * seconds)
        gaps = [stamp(b) - stamp(a) for a, b in itertools.pairwise(full)]
        long_gaps = [round(gap * 1000) for gap in gaps if gap > 0.09]
        measured = (
            f"pace {pace:.3f} over {seconds:.1f} s, longest gap between two odometry stamps "
            f"{max(gaps) * 1000:.0f} ms, gaps over 90 ms: {long_gaps}"
        )
        print(f"test_29: {measured}")
        self.assertGreater(seconds, 18.0, measured)
        self.assertAlmostEqual(pace, 1.0, delta=0.03, msg=measured)
        self.assertLessEqual(len(long_gaps), 1, measured)
        self.assertGreater(len(self.trail[-1][1].poses), 100)

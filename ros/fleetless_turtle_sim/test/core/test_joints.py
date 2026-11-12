# SPDX-License-Identifier: Apache-2.0
"""The model's joints: the legs follow the wheels, the head the turn."""

import math

import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core import joints
from fleetless_turtle_sim.core.drive import Drive
from fleetless_turtle_sim.core.joints import FRONT_LEFT, FRONT_RIGHT, HEAD, HIND_LEFT, HIND_RIGHT
from fleetless_turtle_sim.core.simulation import Simulation
from fleetless_turtle_sim.core.world import cell_centre

LEGS = (FRONT_LEFT, FRONT_RIGHT, HIND_LEFT, HIND_RIGHT)
QUARTER_STRIDE_M = c.LEG_STRIDE_M / 4


def drive_for(drive: Drive, seconds: float, linear: float, angular: float) -> None:
    for _ in range(round(seconds / c.STEP_S)):
        drive.command(linear, angular)
        drive.step(c.STEP_S)


@pytest.fixture
def drive(world):
    """A turtle in the middle of an empty world, heading east."""
    return Drive(world, *cell_centre(128, 128), 0.0)


# --- The joints -------------------------------------------------------------


def test_the_joints_are_a_head_and_four_legs():
    assert joints.NAMES == (HEAD, FRONT_LEFT, FRONT_RIGHT, HIND_LEFT, HIND_RIGHT)
    assert tuple(joints.positions(0.0, 0.0, 0.0)) == joints.NAMES
    assert len(set(joints.NAMES)) == 5


def test_a_turtle_that_has_not_moved_stands_straight():
    assert set(joints.positions(0.0, 0.0, 0.0).values()) == {0.0}


def test_front_and_hind_swing_against_each_other_on_each_side():
    for left_m, right_m in [(0.03, 0.03), (0.11, -0.07), (-0.2, 0.31), (1.234, 5.678)]:
        angles = joints.positions(left_m, right_m, 0.0)
        assert angles[FRONT_LEFT] == -angles[HIND_LEFT] != 0.0
        assert angles[FRONT_RIGHT] == -angles[HIND_RIGHT] != 0.0


def test_a_leg_swings_once_per_stride_and_by_its_swing_either_way():
    assert joints.positions(QUARTER_STRIDE_M, 0.0, 0.0)[FRONT_LEFT] == pytest.approx(c.LEG_SWING)
    assert joints.positions(3 * QUARTER_STRIDE_M, 0.0, 0.0)[FRONT_LEFT] == pytest.approx(
        -c.LEG_SWING
    )
    assert joints.positions(c.LEG_STRIDE_M, 0.0, 0.0)[FRONT_LEFT] == pytest.approx(0.0, abs=1e-12)
    for i in range(200):
        angles = joints.positions(0.013 * i, -0.007 * i, 0.0)
        assert all(abs(angles[leg]) <= c.LEG_SWING for leg in LEGS)


def test_each_sides_legs_follow_that_sides_wheel_alone():
    moved = joints.positions(0.05, 0.0, 0.0)
    assert moved[FRONT_LEFT] != 0.0 and moved[HIND_LEFT] != 0.0
    assert moved[FRONT_RIGHT] == 0.0 and moved[HIND_RIGHT] == 0.0
    moved = joints.positions(0.0, 0.05, 0.0)
    assert moved[FRONT_LEFT] == 0.0 and moved[HIND_LEFT] == 0.0
    assert moved[FRONT_RIGHT] != 0.0 and moved[HIND_RIGHT] != 0.0


def test_driving_straight_a_front_leg_swings_with_the_hind_leg_across_from_it():
    angles = joints.positions(0.04, 0.04, 0.0)
    assert angles[FRONT_LEFT] == angles[HIND_RIGHT] > 0.0
    assert angles[FRONT_RIGHT] == angles[HIND_LEFT] < 0.0


def test_a_wheel_that_rolls_backwards_walks_its_legs_backwards():
    forwards = joints.positions(0.04, 0.04, 0.0)
    backwards = joints.positions(-0.04, -0.04, 0.0)
    assert all(backwards[leg] == -forwards[leg] for leg in LEGS)


def test_the_head_turns_into_the_curve_by_30_degrees_at_most():
    assert joints.positions(0.0, 0.0, 0.0)[HEAD] == 0.0
    left = joints.positions(0.0, 0.0, c.ANGULAR_SPEED_LIMIT)[HEAD]
    assert left == pytest.approx(math.radians(30.0)) == c.HEAD_TURN_LIMIT
    assert joints.positions(0.0, 0.0, -c.ANGULAR_SPEED_LIMIT)[HEAD] == pytest.approx(-left)
    # In proportion to the turn rate, and never further.
    assert joints.positions(0.0, 0.0, c.ANGULAR_SPEED_LIMIT / 2)[HEAD] == pytest.approx(left / 2)
    assert joints.positions(0.0, 0.0, 100.0)[HEAD] == pytest.approx(left)
    assert joints.positions(0.0, 0.0, -100.0)[HEAD] == pytest.approx(-left)


# --- The wheels the legs follow ---------------------------------------------


def test_driving_straight_rolls_both_wheels_as_far_as_the_turtle_drives(drive):
    drive_for(drive, 3.0, 0.4, 0.0)
    assert drive.left_wheel == pytest.approx(drive.odometer)
    assert drive.right_wheel == pytest.approx(drive.odometer)
    assert drive.odometer > 1.0
    drive_for(drive, 3.0, -0.4, 0.0)
    assert drive.left_wheel == drive.right_wheel < 0.3


def test_turning_on_the_spot_walks_one_side_backwards(drive):
    drive_for(drive, 2.0, 0.0, 1.5)  # counter-clockwise: to the left
    assert drive.odometer == 0.0
    assert drive.left_wheel < 0.0 < drive.right_wheel
    assert drive.left_wheel == pytest.approx(-drive.right_wheel)
    # The left side's legs are where driving backwards would have put them,
    # the right side's where driving forwards would have.
    angles = joints.positions(drive.left_wheel, drive.right_wheel, 0.0)
    backwards = joints.positions(drive.left_wheel, drive.left_wheel, 0.0)
    forwards = joints.positions(drive.right_wheel, drive.right_wheel, 0.0)
    assert (angles[FRONT_LEFT], angles[HIND_LEFT]) == (backwards[FRONT_LEFT], backwards[HIND_LEFT])
    assert (angles[FRONT_RIGHT], angles[HIND_RIGHT]) == (
        forwards[FRONT_RIGHT],
        forwards[HIND_RIGHT],
    )


def test_the_wheels_roll_apart_by_the_track_width_times_the_turn(drive):
    turned = 0.0
    for linear, angular, seconds in [(0.3, 0.8, 2.0), (-0.2, -1.7, 1.5), (0.5, 0.0, 1.0)]:
        for _ in range(round(seconds / c.STEP_S)):
            drive.command(linear, angular)
            drive.step(c.STEP_S)
            turned += drive.angular * c.STEP_S
    assert turned != 0.0
    assert drive.right_wheel - drive.left_wheel == pytest.approx(c.TRACK_WIDTH_M * turned)


def test_a_turtle_that_pushes_against_a_wall_does_not_walk_on_the_spot(kinds, world):
    kinds[:, 135:137] = c.WALL
    from fleetless_turtle_sim.core.world import World

    drive = Drive(World(kinds), *cell_centre(128, 128), 0.0)
    drive_for(drive, 5.0, 0.5, 0.0)
    assert drive.bumper
    rolled = drive.left_wheel
    assert rolled == pytest.approx(drive.odometer)
    drive_for(drive, 2.0, 0.5, 0.0)
    assert drive.left_wheel == drive.right_wheel == rolled


# --- In the simulation ------------------------------------------------------


def test_the_simulation_reports_the_joints_as_it_drives(world):
    sim = Simulation(world)
    assert sim.joints() == joints.positions(0.0, 0.0, 0.0)
    for _ in range(100):
        sim.command(0.3, 1.0)
        sim.step()
    angles = sim.joints()
    assert tuple(angles) == joints.NAMES
    assert angles == joints.positions(sim.drive.left_wheel, sim.drive.right_wheel, 1.0)
    assert angles[HEAD] == pytest.approx(c.HEAD_TURN_LIMIT / 2)
    assert angles[FRONT_LEFT] != angles[FRONT_RIGHT]


def test_a_reset_stands_the_legs_straight_and_a_teleport_leaves_them(world):
    sim = Simulation(world)
    for _ in range(40):
        sim.command(0.4, 0.0)
        sim.step()
    swung = sim.joints()
    assert swung[FRONT_LEFT] != 0.0
    assert sim.teleport(100, 100, 90.0)[0]
    assert sim.joints() == swung
    sim.reset()
    assert set(sim.joints().values()) == {0.0}

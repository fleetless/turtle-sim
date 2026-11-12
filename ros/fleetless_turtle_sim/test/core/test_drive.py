# SPDX-License-Identifier: Apache-2.0
"""The drive: speeds, limits, the command timeout, collisions and the bumper."""

import math

import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.drive import Drive
from fleetless_turtle_sim.core.world import World

DT = c.STEP_S


def steps(seconds: float) -> int:
    return round(seconds / DT)


def run(drive: Drive, seconds: float, linear: float = 0.0, angular: float = 0.0, **kwargs) -> None:
    """Hold a command for a while, as a publisher at 50 Hz would."""
    for _ in range(steps(seconds)):
        drive.command(linear, angular)
        drive.step(DT, **kwargs)


@pytest.fixture
def drive(world):
    """A turtle in the open, at (5 m, 5 m), heading east."""
    return Drive(world, 5.0, 5.0, 0.0)


@pytest.fixture
def walled(kinds):
    """A wall one cell thick at x = 6.0 to 6.1 m, from south to north."""
    kinds[:, 60] = c.WALL
    return World(kinds)


# --- Driving ----------------------------------------------------------------


def test_it_stands_until_told_otherwise(drive):
    for _ in range(100):
        drive.step(DT)
    assert (drive.x, drive.y, drive.heading) == (5.0, 5.0, 0.0)
    assert (drive.linear, drive.angular, drive.odometer) == (0.0, 0.0, 0.0)
    assert not drive.bumper and drive.collisions == 0


def test_straight_ahead(drive):
    run(drive, 2.0, linear=0.5)
    # 25 steps of speeding up cover 0.13 m, the 75 at full speed 0.75 m.
    assert drive.linear == pytest.approx(0.5)
    assert drive.x == pytest.approx(5.88)
    assert drive.y == 5.0 and drive.heading == 0.0
    assert drive.odometer == pytest.approx(0.88)


def test_backwards(drive):
    run(drive, 2.0, linear=-0.5)
    assert drive.linear == pytest.approx(-0.5)
    assert drive.x == pytest.approx(5.0 - 0.88)
    assert drive.odometer == pytest.approx(0.88)


def test_along_the_heading(world):
    north = Drive(world, 5.0, 5.0, math.pi / 2)
    run(north, 2.0, linear=0.5)
    assert north.x == pytest.approx(5.0)
    assert north.y == pytest.approx(5.88)


def test_turning_on_the_spot(drive):
    run(drive, 1.0, angular=2.0)
    assert (drive.x, drive.y) == (5.0, 5.0)
    assert drive.odometer == 0.0
    assert drive.angular == pytest.approx(2.0)
    # 17 steps of speeding up, the rest at 2 rad/s.
    spin_up = sum(min(0.12 * i, 2.0) for i in range(1, 51)) * DT
    assert drive.heading == pytest.approx(spin_up)
    run(drive, 1.0, angular=-2.0)
    assert drive.angular == pytest.approx(-2.0)


def test_the_heading_stays_within_one_turn(drive):
    run(drive, 10.0, angular=2.0)
    assert -math.pi < drive.heading <= math.pi


def test_a_curve(drive):
    run(drive, 3.0, linear=0.5, angular=1.0)
    assert drive.heading != 0.0 and drive.y != 5.0
    # Nearly a circle of radius v / w = 0.5 m, so it never gets far.
    assert math.hypot(drive.x - 5.0, drive.y - 5.5) == pytest.approx(0.5, abs=0.06)


# --- Limits -----------------------------------------------------------------


@pytest.mark.parametrize("sign", [1, -1])
def test_commands_are_clamped_to_the_speed_limits(drive, sign):
    run(drive, 3.0, linear=sign * 5.0, angular=sign * 50.0)
    assert drive.linear == pytest.approx(sign * c.LINEAR_SPEED_LIMIT)
    assert drive.angular == pytest.approx(sign * c.ANGULAR_SPEED_LIMIT)


def test_speeds_change_no_faster_than_the_acceleration_limits(drive):
    plan = [(0.5, 2.0)] * 40 + [(-0.5, -2.0)] * 80 + [(0.0, 0.0)] * 40 + [(9.0, -9.0)] * 40
    for linear, angular in plan:
        before = (drive.linear, drive.angular)
        drive.command(linear, angular)
        drive.step(DT)
        assert abs(drive.linear - before[0]) <= c.LINEAR_ACCELERATION_LIMIT * DT + 1e-12
        assert abs(drive.angular - before[1]) <= c.ANGULAR_ACCELERATION_LIMIT * DT + 1e-12


def test_full_speed_takes_half_a_second(drive):
    for i in range(1, 26):
        drive.command(0.5, 0.0)
        drive.step(DT)
        assert drive.linear == pytest.approx(0.02 * i)
    drive.command(0.5, 0.0)
    drive.step(DT)
    assert drive.linear == pytest.approx(0.5)


def test_crawling_reduces_both_limits(drive):
    run(drive, 3.0, linear=0.5, angular=2.0, crawling=True)
    assert drive.linear == pytest.approx(c.LINEAR_SPEED_LIMIT * c.CRAWL_LINEAR_FACTOR)
    assert drive.linear == pytest.approx(0.05)
    assert drive.angular == pytest.approx(c.ANGULAR_SPEED_LIMIT * c.CRAWL_ANGULAR_FACTOR)
    assert drive.angular == pytest.approx(0.5)


def test_a_turtle_that_starts_crawling_slows_down_under_its_limit(drive):
    run(drive, 1.0, linear=0.5)
    drive.command(0.5, 0.0)
    drive.step(DT, crawling=True)
    assert drive.linear == pytest.approx(0.48)  # not 0.05 at once
    run(drive, 1.0, linear=0.5, crawling=True)
    assert drive.linear == pytest.approx(0.05)


# --- The command timeout ----------------------------------------------------


def test_a_command_is_good_for_half_a_second(drive):
    drive.command(0.5, 0.0)
    for _ in range(steps(0.5)):
        drive.step(DT)
    assert drive.linear == pytest.approx(0.5)  # still speeding up to the end
    drive.step(DT)
    assert drive.linear == pytest.approx(0.48)  # the target is zero now
    for _ in range(steps(0.5)):
        drive.step(DT)
    assert drive.linear == 0.0
    x = drive.x
    for _ in range(steps(1.0)):
        drive.step(DT)
    assert drive.x == x and drive.linear == 0.0


def test_the_timeout_stops_a_turn_as_well(drive):
    drive.command(0.0, 2.0)
    for _ in range(steps(0.5 + 2.0 / 6.0) + 2):
        drive.step(DT)
    assert drive.angular == 0.0


def test_a_new_command_restarts_the_half_second(drive):
    for _ in range(4):
        drive.command(0.5, 0.0)
        for _ in range(steps(0.4)):
            drive.step(DT)
    assert drive.linear == pytest.approx(0.5)


def test_a_zero_command_stops_under_the_acceleration_limit(drive):
    run(drive, 1.0, linear=0.5)
    run(drive, 0.3, linear=0.0)
    assert drive.linear == pytest.approx(0.2)
    run(drive, 0.3, linear=0.0)
    assert drive.linear == 0.0


# --- Non-finite input -------------------------------------------------------


def test_a_non_finite_value_counts_as_zero(drive):
    for linear, angular in [(math.nan, 0.0), (math.inf, 0.0), (-math.inf, math.nan)]:
        drive.command(linear, angular)
        for _ in range(10):
            drive.step(DT)
    assert (drive.x, drive.y, drive.heading) == (5.0, 5.0, 0.0)
    assert drive.non_finite_commands == 3


def test_the_finite_half_of_a_command_still_counts(drive):
    run(drive, 1.0, linear=math.nan, angular=1.0)
    assert drive.x == 5.0 and drive.linear == 0.0
    assert drive.angular == pytest.approx(1.0)
    assert drive.non_finite_commands == steps(1.0)


def test_finite_commands_are_not_counted(drive):
    run(drive, 1.0, linear=0.3, angular=-0.3)
    assert drive.non_finite_commands == 0


# --- Collision --------------------------------------------------------------


def test_a_wall_stops_the_turtle_without_touching_it(walled):
    drive = Drive(walled, 5.0, 5.0, 0.0)
    run(drive, 4.0, linear=0.5)
    # The wall's face is at 6.0 m; the footprint may rest against it.
    assert drive.x + c.TURTLE_RADIUS_M <= 6.0 + 1e-6
    assert drive.x + c.TURTLE_RADIUS_M > 6.0 - 0.011  # and within a step of it
    assert walled.fits(drive.x, drive.y)
    assert drive.y == 5.0
    assert drive.bumper and drive.collisions == 1


def test_a_refused_move_sets_the_linear_speed_to_zero(walled):
    drive = Drive(walled, 5.8, 5.0, 0.0)
    refused = False
    for _ in range(steps(2.0)):
        drive.command(0.5, 0.0)
        before = drive.x
        was_refused = drive.collisions
        drive.step(DT)
        if drive.collisions > was_refused:
            refused = True
            assert drive.linear == 0.0 and drive.x == before
    assert refused


def test_pushing_adds_nothing_to_the_odometer(walled):
    drive = Drive(walled, 5.0, 5.0, 0.0)
    run(drive, 4.0, linear=0.5)
    driven = drive.odometer
    assert driven == pytest.approx(drive.x - 5.0)
    run(drive, 2.0, linear=0.5)
    assert drive.odometer - driven < 0.011


def test_there_is_no_sliding_along_a_wall(walled):
    drive = Drive(walled, 5.5, 5.0, math.radians(45))
    run(drive, 3.0, linear=0.5)
    assert drive.bumper
    x, y = drive.x, drive.y
    run(drive, 3.0, linear=0.5)
    assert drive.x == pytest.approx(x, abs=0.011)
    assert drive.y == pytest.approx(y, abs=0.011)  # no creeping north


def test_the_grids_edge_is_a_wall(world):
    drive = Drive(world, 0.5, 5.0, math.pi)  # heading west
    run(drive, 3.0, linear=0.5)
    assert c.TURTLE_RADIUS_M - 1e-6 <= drive.x < c.TURTLE_RADIUS_M + 0.011
    assert drive.bumper and drive.collisions == 1
    north = Drive(world, 5.0, 25.2, math.pi / 2)
    run(north, 3.0, linear=0.5)
    assert 25.6 - c.TURTLE_RADIUS_M - 0.011 < north.y <= 25.6 - c.TURTLE_RADIUS_M + 1e-6


def test_the_turtle_turns_while_it_is_blocked(walled):
    drive = Drive(walled, 5.0, 5.0, 0.0)
    run(drive, 4.0, linear=0.5)
    x, heading = drive.x, drive.heading
    run(drive, 1.0, linear=0.5, angular=2.0)
    assert drive.heading != heading
    assert drive.x == pytest.approx(x, abs=0.011)
    # Turned around, it drives away.
    drive.place(drive.x, drive.y, math.pi)
    run(drive, 1.0, linear=0.5)
    assert drive.x < x - 0.3


@pytest.mark.parametrize("offset_mm", range(0, 100, 7))
def test_nothing_is_tunnelled_through_at_full_speed(walled, offset_mm):
    drive = Drive(walled, 5.0 + offset_mm / 1000, 5.0, 0.0)
    for _ in range(steps(6.0)):
        drive.command(0.5, 0.0)
        drive.step(DT)
        assert drive.x < 6.0
    backwards = Drive(walled, 7.0 + offset_mm / 1000, 5.0, 0.0)
    for _ in range(steps(6.0)):
        backwards.command(-0.5, 0.0)
        backwards.step(DT)
        assert backwards.x > 6.1


# --- The bumper -------------------------------------------------------------


def test_the_bumper_holds_for_a_fifth_of_a_second(walled):
    drive = Drive(walled, 5.8, 5.0, 0.0)
    while not drive.bumper:
        drive.command(0.5, 0.0)
        drive.step(DT)
    assert drive.collisions == 1
    # The refusal was this step. Standing still, the bumper lets go 0.2 s later.
    held = 1
    for _ in range(50):
        drive.command(0.0, 0.0)
        drive.step(DT)
        held += drive.bumper
    assert held == steps(c.BUMPER_HOLD_S)
    assert not drive.bumper


def test_one_long_push_is_one_collision_and_the_next_push_another(walled):
    drive = Drive(walled, 5.8, 5.0, 0.0)
    run(drive, 3.0, linear=0.5)
    assert drive.bumper and drive.collisions == 1
    run(drive, 1.0, linear=0.0)
    assert not drive.bumper
    run(drive, 1.0, linear=0.5)
    assert drive.bumper and drive.collisions == 2


def test_backing_away_ends_the_bumper(walled):
    drive = Drive(walled, 5.8, 5.0, 0.0)
    run(drive, 3.0, linear=0.5)
    run(drive, 1.0, linear=-0.5)
    assert not drive.bumper and drive.collisions == 1
    assert drive.x < 5.8


# --- Placing ----------------------------------------------------------------


def test_place_puts_the_turtle_down_standing(drive):
    run(drive, 1.0, linear=0.5, angular=1.0)
    odometer = drive.odometer
    drive.place(10.05, 12.05, math.radians(270))
    assert (drive.x, drive.y) == (10.05, 12.05)
    assert drive.heading == pytest.approx(-math.pi / 2)
    assert (drive.linear, drive.angular) == (0.0, 0.0)
    assert drive.odometer == odometer
    for _ in range(10):
        drive.step(DT)
    assert (drive.x, drive.y) == (10.05, 12.05)  # the old command is gone too

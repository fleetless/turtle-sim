# SPDX-License-Identifier: Apache-2.0
"""Goals in the simulation: every row of the reason table, for a drive to a
cell, a drive into a square and a dock; and the rules all three share."""

import itertools
import math

import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.goals import (
    DockFeedback,
    DockResult,
    DriveFeedback,
    DriveResult,
)
from fleetless_turtle_sim.core.simulation import Simulation
from fleetless_turtle_sim.core.world import World, cell_centre

DT = c.STEP_S


def run(sim, goal, seconds=600.0):
    """Step until the goal has ended, or for `seconds`. Returns the seconds it took."""
    steps = 0
    while goal.result is None and steps * DT < seconds:
        sim.step()
        steps += 1
    return steps * DT


def wait(sim, seconds):
    for _ in range(round(seconds / DT)):
        sim.step()


def standing(sim):
    return sim.drive.linear == 0.0 and sim.drive.angular == 0.0


def enclose(kinds, x_min=100, y_min=100, x_max=120, y_max=120):
    """A walled yard without a door."""
    kinds[y_min, x_min : x_max + 1] = kinds[y_max, x_min : x_max + 1] = c.WALL
    kinds[y_min : y_max + 1, x_min] = kinds[y_min : y_max + 1, x_max] = c.WALL


@pytest.fixture
def sim(world):
    return Simulation(world)


def driving(sim, goal, seconds=3.0):
    """A goal that has been under way for a while."""
    wait(sim, seconds)
    assert goal.result is None and sim.goal is goal
    assert goal.feedback().phase == "driving" and sim.drive.linear > 0.4
    return goal


# --- reached ----------------------------------------------------------------


def test_a_drive_to_a_cell_ends_reached_on_its_centre(sim):
    goal = sim.drive_to(40, 24)
    assert goal.kind == "drive_to" and goal.result is None and sim.goal is goal
    seconds = run(sim, goal)
    result = goal.result
    assert result == DriveResult(
        success=True,
        reason="reached",
        message="The turtle reached cell (40, 24).",
        cell_x=40,
        cell_y=24,
        distance_m=pytest.approx(math.hypot(3.2, 1.6), abs=0.01),
        duration_s=pytest.approx(seconds - DT),  # it ended with the last step's first look
    )
    assert math.dist((sim.drive.x, sim.drive.y), cell_centre(40, 24)) < c.GOAL_TOLERANCE_M
    assert standing(sim) and sim.goal is None and sim.mode == "idle"
    assert type(result.distance_m) is float and type(result.cell_x) is int
    # It stays there.
    wait(sim, 2.0)
    assert sim.status().cell_x == 40 and standing(sim)


def test_a_drive_into_a_square_ends_reached_on_the_cell_nearest_its_centre(sim):
    goal = sim.drive_to_square("C2")
    assert goal.kind == "drive_to_square"
    run(sim, goal)
    result = goal.result
    # C2 is x 32-47, y 16-31. Its centre is a corner between four cells; the
    # lower row wins, then the lower column.
    assert (result.success, result.reason) == (True, "reached")
    assert result.message == "The turtle reached square C2 at cell (39, 23)."
    assert (result.cell_x, result.cell_y) == (39, 23)
    assert sim.status().square == "C2"


def test_a_square_whose_middle_is_taken_is_reached_on_the_nearest_cell_that_is_not(kinds):
    kinds[20:28, 36:44] = c.ROCK  # the middle of C2
    sim = Simulation(World(kinds))
    goal = sim.drive_to_square("C2")
    run(sim, goal)
    assert goal.result.reason == "reached"
    x, y = goal.result.cell_x, goal.result.cell_y
    assert 32 <= x <= 47 and 16 <= y <= 31
    assert sim.world.traversable(x, y)
    # No traversable cell of the square is nearer to its centre.
    centre = (4.0, 2.4)
    nearest = min(
        math.dist(cell_centre(cx, cy), centre)
        for cx in range(32, 48)
        for cy in range(16, 32)
        if sim.world.traversable(cx, cy)
    )
    assert math.dist(cell_centre(x, y), centre) == pytest.approx(nearest)


def test_a_drive_to_where_the_turtle_stands_is_reached_at_once(sim):
    goal = sim.drive_to(8, 8)
    assert run(sim, goal) <= 2 * DT
    assert goal.result.reason == "reached" and goal.result.distance_m == 0.0


# --- charged ----------------------------------------------------------------


def test_a_dock_drives_to_the_pads_start_cell_and_waits_for_the_battery(sim):
    assert sim.teleport(60, 8, 0.0)[0]
    sim.battery.percent = 40.0
    goal = sim.dock(80.0)
    assert goal.kind == "dock" and sim.mode == "docking"
    feedback = goal.feedback()
    assert feedback == DockFeedback(
        phase="driving", battery_percent=40.0, remaining_m=pytest.approx(5.2)
    )
    phases = []
    while goal.result is None:
        sim.step()
        phase = goal.feedback().phase
        if not phases or phases[-1] != phase:
            phases.append(phase)
        assert sim.mode == "docking" or goal.result is not None
    assert phases == ["driving", "charging"]
    result = goal.result
    assert isinstance(result, DockResult)
    assert (result.success, result.reason) == (True, "charged")
    assert result.message == "The turtle stands on the charging pad and the battery is at 80 %."
    assert result.battery_percent == pytest.approx(80.0, abs=0.05)
    assert sim.cell == sim.world.start_cell == (8, 8)
    # Five metres of driving, then about 40 % at 100 % a minute.
    assert 30.0 < result.duration_s < 45.0
    assert sim.mode == "charging" and sim.goal is None


def test_a_dock_succeeds_at_once_if_the_battery_is_already_there_on_arrival(sim):
    assert sim.teleport(30, 8, 180.0)[0]
    goal = sim.dock(50.0)
    seconds = run(sim, goal)
    assert goal.result.reason == "charged"
    assert goal.result.battery_percent > 99.0
    # The drive, and not a step of waiting.
    assert seconds < 7.0 and sim.cell == (8, 8)


def test_a_dock_on_the_start_cell_with_a_full_battery_ends_at_once(sim):
    goal = sim.dock(100.0)
    assert run(sim, goal) <= 2 * DT
    assert goal.result.reason == "charged"
    assert goal.result.message == (
        "The turtle stands on the charging pad and the battery is at 100 %."
    )


def test_a_dock_to_a_full_battery_waits_until_it_is_full(sim):
    sim.battery.percent = 10.0
    goal = sim.dock(100.0)
    seconds = run(sim, goal)
    assert goal.result.reason == "charged" and sim.battery.percent == 100.0
    assert seconds == pytest.approx(0.9 * c.BATTERY_CHARGING_S, abs=0.1)


# --- out_of_range -----------------------------------------------------------


@pytest.mark.parametrize(("x", "y"), [(-1, 5), (256, 5), (5, -1), (5, 256), (300, 300)])
def test_a_cell_outside_the_grid_is_out_of_range(sim, x, y):
    goal = sim.drive_to(x, y)
    assert goal.result == DriveResult(
        success=False,
        reason="out_of_range",
        message=f"Cell ({x}, {y}) is outside the grid, 0 to 255.",
        cell_x=8,
        cell_y=8,
        distance_m=0.0,
        duration_s=0.0,
    )
    assert sim.goal is None and sim.mode == "idle"


@pytest.mark.parametrize("name", ["Z9", "A0", "A17", "Q1", "a1", "", "A1 ", "C07", "7C", "P 16"])
def test_a_string_that_names_no_square_is_out_of_range(sim, name):
    goal = sim.drive_to_square(name)
    assert (goal.result.success, goal.result.reason) == (False, "out_of_range")
    assert goal.result.message == f'"{name}" names no square: they run from A1 to P16.'
    assert sim.goal is None


def test_the_sentence_stays_one_line_whatever_was_sent(sim):
    goal = sim.drive_to_square("to the pond, please, and step on it" * 40)
    assert goal.result.message == (
        '"to the pond, ple..." names no square: they run from A1 to P16.'
    )
    # A square's name with a line break behind it is no square's name.
    goal = sim.drive_to_square("A1\n")
    assert goal.result.reason == "out_of_range"
    assert goal.result.message == '"A1?" names no square: they run from A1 to P16.'
    assert sim.drive_to_square("C7\x00\t").result.message.startswith('"C7??" names no square')


@pytest.mark.parametrize("target", [0.0, 0.99, 100.01, 150.0, -5.0, math.nan, math.inf])
def test_a_target_charge_outside_1_to_100_is_out_of_range(sim, target):
    goal = sim.dock(target)
    result = goal.result
    assert (result.success, result.reason) == (False, "out_of_range")
    assert result.message == f"A target charge of {target:g} % is outside 1 to 100."
    assert (result.battery_percent, result.duration_s) == (100.0, 0.0)
    assert sim.goal is None


@pytest.mark.parametrize("target", [1.0, 100.0, 50.5])
def test_the_bounds_of_the_target_charge_are_in_range(sim, target):
    assert sim.dock(target).result is None


# --- target_occupied --------------------------------------------------------


def test_a_target_that_blocks_is_occupied_and_the_sentence_names_what_is_there(kinds):
    kinds[40, 12] = c.ROCK
    kinds[41, 12] = c.WALL
    kinds[42, 12] = c.WATER
    sim = Simulation(World(kinds))
    for cell, what in [((12, 40), "a rock"), ((12, 41), "a wall"), ((12, 42), "water")]:
        goal = sim.drive_to(*cell)
        assert (goal.result.success, goal.result.reason) == (False, "target_occupied")
        assert goal.result.message == "Cell ({}, {}) is {}.".format(*cell, what)
    assert sim.goal is None and standing(sim)


def test_a_target_the_turtle_does_not_fit_on_is_occupied(kinds):
    kinds[40, 12] = c.ROCK
    sim = Simulation(World(kinds))
    goal = sim.drive_to(13, 41)  # free, and diagonal to the rock
    assert goal.result.reason == "target_occupied"
    assert goal.result.message == "The turtle does not fit on cell (13, 41)."
    assert sim.drive_to(0, 100).result.message == "The turtle does not fit on cell (0, 100)."
    assert sim.drive_to(14, 42).result is None


def test_a_square_without_a_traversable_cell_is_occupied(kinds):
    kinds[16:32, 32:48] = c.WATER  # all of C2 is a pond
    sim = Simulation(World(kinds))
    goal = sim.drive_to_square("C2")
    assert (goal.result.success, goal.result.reason) == (False, "target_occupied")
    assert goal.result.message == "Square C2 has no cell the turtle fits on."


def test_a_block_beside_the_target_while_the_turtle_drives_makes_it_occupied(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    assert sim.set_block(121, 8, True)[0]
    seconds = run(sim, goal, seconds=2.0)
    assert goal.result.reason == "target_occupied"
    assert goal.result.message == "The turtle does not fit on cell (120, 8)."
    assert seconds < 1.0 and standing(sim)  # it braked first, then looked


def test_a_square_whose_cell_gets_blocked_is_reached_on_another_of_its_cells(sim):
    goal = driving(sim, sim.drive_to_square("H1"))  # x 112-127, y 0-15
    first = goal.feedback()
    assert sim.set_block(119, 7, True)[0]
    run(sim, goal)
    assert goal.result.reason == "reached"
    cell = (goal.result.cell_x, goal.result.cell_y)
    assert cell != (119, 7) and 112 <= cell[0] <= 127 and 0 <= cell[1] <= 15
    assert goal.result.message == "The turtle reached square H1 at cell ({}, {}).".format(*cell)
    assert first.remaining_m > 0.0


# --- unreachable ------------------------------------------------------------


def test_no_path_at_the_start_is_unreachable(kinds):
    enclose(kinds)
    sim = Simulation(World(kinds))
    goal = sim.drive_to(110, 110)
    assert goal.result is None  # accepted: the answer comes with the first step
    assert goal.feedback().phase == "planning"
    assert run(sim, goal) == DT
    assert goal.result == DriveResult(
        success=False,
        reason="unreachable",
        message="No path leads from cell (8, 8) to cell (110, 110).",
        cell_x=8,
        cell_y=8,
        distance_m=0.0,
        duration_s=pytest.approx(0.0),
    )
    assert sim.goal is None and sim.plan == ()


def test_a_square_inside_a_yard_without_a_door_is_unreachable(kinds):
    enclose(kinds, 110, 14, 130, 34)  # round square H2, x 112-127, y 16-31
    sim = Simulation(World(kinds))
    goal = sim.drive_to_square("H2")
    run(sim, goal)
    assert goal.result.reason == "unreachable"
    assert goal.result.message == "No path leads from cell (8, 8) to square H2."


def test_no_path_when_replanning_is_unreachable(kinds):
    kinds[100:102, :] = c.WALL  # a wall across the world, with one doorway
    kinds[100:102, 60:68] = c.FREE
    sim = Simulation(World(kinds))
    assert sim.teleport(64, 60, 90.0)[0]
    goal = driving(sim, sim.drive_to(64, 140), seconds=2.0)
    for x in range(60, 68):
        assert sim.set_block(x, 100, True)[0]
    assert goal.feedback().phase == "driving"
    sim.step()
    assert goal.feedback().phase == "replanning"
    run(sim, goal)
    result = goal.result
    assert (result.success, result.reason) == (False, "unreachable")
    x, y = sim.cell
    assert result.message == f"No path leads from cell ({x}, {y}) to cell (64, 140)."
    assert (result.cell_x, result.cell_y) == (x, y) and y < 100
    assert result.distance_m > 0.5 and standing(sim) and sim.drive.collisions == 0


def test_a_dock_from_a_yard_without_a_door_is_unreachable(kinds):
    enclose(kinds)
    sim = Simulation(World(kinds))
    assert sim.teleport(110, 110, 0.0)[0]
    goal = sim.dock(100.0)
    run(sim, goal)
    assert goal.result.reason == "unreachable"
    assert goal.result.message == "No path leads from cell (110, 110) to the charging pad."


# --- Replanning -------------------------------------------------------------


def test_a_block_on_the_path_sends_the_turtle_round_it(sim):
    assert sim.teleport(20, 50, 0.0)[0]
    goal = driving(sim, sim.drive_to(120, 50))
    version = sim.plan_version
    for y in range(40, 61):
        assert sim.set_block(80, y, True)[0]  # a row of rocks across the line
    phases = []
    while goal.result is None:
        sim.step()
        phase = goal.feedback().phase
        if not phases or phases[-1] != phase:
            phases.append(phase)
    assert phases == ["replanning", "driving"]
    assert goal.result.reason == "reached" and sim.drive.collisions == 0
    assert sim.plan_version == version + 2  # the new path, and none
    # Further than the straight line: it went round.
    assert goal.result.distance_m > 10.0 + 0.3


# --- stuck ------------------------------------------------------------------


def test_a_turtle_that_gets_nowhere_is_stuck_after_five_seconds(sim):
    assert sim.teleport(50, 50, 0.0)[0]
    goal = driving(sim, sim.drive_to(50, 120))
    sim.world.fits = lambda x, y: False  # held by something the map does not show
    held_in = sim.cell
    seconds = run(sim, goal)
    assert seconds == pytest.approx(c.STUCK_WINDOW_S, abs=2 * DT)
    result = goal.result
    assert (result.success, result.reason) == (False, "stuck")
    assert result.message == "The turtle made no progress for 5 s on the way to cell (50, 120)."
    assert (result.cell_x, result.cell_y) == held_in
    assert sim.goal is None


def test_a_dock_can_get_stuck_too(sim):
    assert sim.teleport(60, 8, 180.0)[0]
    goal = sim.dock(100.0)
    sim.world.fits = lambda x, y: False
    run(sim, goal)
    assert goal.result.reason == "stuck"
    assert goal.result.message == (
        "The turtle made no progress for 5 s on the way to the charging pad."
    )


# --- preempted --------------------------------------------------------------


def test_a_new_goal_ends_the_running_one_before_it_starts(sim):
    first = driving(sim, sim.drive_to(120, 8))
    second = sim.drive_to(8, 60)
    assert first.result == DriveResult(
        success=False,
        reason="preempted",
        message="Another goal was accepted on the way to cell (120, 8).",
        cell_x=sim.cell[0],
        cell_y=8,
        distance_m=pytest.approx(sim.drive.odometer),
        duration_s=pytest.approx(3.0),
    )
    assert sim.goal is second and second.result is None
    # The new one starts from a stand: the turtle brakes, then the plan is made.
    assert second.feedback().phase == "planning"
    run(sim, second)
    assert second.result.reason == "reached"
    assert second.result.distance_m < first.result.distance_m + 7.0


@pytest.mark.parametrize("kind", ["drive_to", "drive_to_square", "dock"])
def test_each_kind_of_goal_preempts_and_is_preempted(sim, kind):
    start = {
        "drive_to": lambda: sim.drive_to(120, 60),
        "drive_to_square": lambda: sim.drive_to_square("H4"),
        "dock": lambda: sim.dock(100.0),
    }
    assert sim.teleport(60, 60, 0.0)[0]
    running = start[kind]()
    wait(sim, 3.0)
    assert running.result is None
    for other in start.values():
        following = other()
        assert running.result.reason == "preempted" and not running.result.success
        assert sim.goal is following
        running = following
        wait(sim, 1.0)


def test_a_goal_that_asks_for_the_impossible_preempts_all_the_same(sim):
    # Every goal is accepted, and a newly accepted one ends the one that
    # runs. What the new one asked for is in its own result.
    first = driving(sim, sim.drive_to(120, 8))
    second = sim.drive_to(999, 8)
    assert first.result.reason == "preempted"
    assert second.result.reason == "out_of_range"
    assert sim.goal is None
    wait(sim, 1.0)
    assert standing(sim)


def test_a_preempted_dock_says_which_phase_it_was_in(sim):
    sim.battery.percent = 5.0
    charging = sim.dock(100.0)
    wait(sim, 1.0)
    assert charging.feedback().phase == "charging"
    sim.drive_to(40, 24)
    assert charging.result.reason == "preempted"
    assert charging.result.message == (
        "Another goal was accepted while the turtle charged on the pad."
    )

    assert sim.teleport(60, 8, 180.0)[0]
    on_the_way = sim.dock(100.0)
    wait(sim, 2.0)
    sim.drive_to(40, 24)
    assert on_the_way.result.message == (
        "Another goal was accepted on the way to the charging pad."
    )


# --- teleop_override --------------------------------------------------------


def test_a_velocity_command_other_than_zero_ends_a_goal(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    sim.command(0.0, 0.5)
    assert goal.result.reason == "teleop_override" and not goal.result.success
    assert goal.result.message == "A velocity command took over on the way to cell (120, 8)."
    assert sim.goal is None and sim.mode == "teleop"
    # And the command is the one that drives now.
    wait(sim, 0.3)
    assert sim.drive.angular == pytest.approx(0.5)
    assert sim.drive.linear < 0.25  # the goal's speed is being braked away


@pytest.mark.parametrize(("linear", "angular"), [(0.2, 0.0), (-0.2, 0.0), (0.0, -1.0), (1e-6, 0.0)])
def test_any_non_zero_command_is_a_person_at_the_controls(sim, linear, angular):
    goal = driving(sim, sim.drive_to_square("H1"))
    sim.command(linear, angular)
    assert goal.result.reason == "teleop_override"
    assert goal.result.message == "A velocity command took over on the way to square H1."


def test_a_zero_twist_ends_nothing_and_does_not_take_the_wheel(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    # The bridge's failsafe: zero Twists, as long as a publisher is silent.
    for _ in range(100):
        sim.command(0.0, 0.0)
        sim.step()
    assert goal.result is None and sim.goal is goal and sim.mode == "auto"
    assert sim.drive.linear == pytest.approx(c.LINEAR_SPEED_LIMIT)  # not a blink
    run(sim, goal)
    assert goal.result.reason == "reached"


def test_a_value_that_is_no_number_counts_as_zero_for_a_goal_too(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    for _ in range(10):
        sim.command(math.nan, math.inf)
        sim.step()
    assert goal.result is None and sim.drive.non_finite_commands == 10
    assert sim.drive.linear == pytest.approx(c.LINEAR_SPEED_LIMIT)
    # One half of the command is a number other than zero: a person.
    sim.command(math.nan, 0.5)
    assert goal.result.reason == "teleop_override"
    assert sim.drive.non_finite_commands == 11


def test_a_command_ends_a_dock_that_charges(sim):
    sim.battery.percent = 5.0
    goal = sim.dock(100.0)
    wait(sim, 1.0)
    sim.command(0.3, 0.0)
    assert goal.result.reason == "teleop_override"
    assert (
        goal.result.message == "A velocity command took over while the turtle charged on the pad."
    )
    assert goal.result.battery_percent == pytest.approx(5.0 + 100.0 / 60.0, abs=0.1)


# --- teleported -------------------------------------------------------------


def test_a_teleport_that_succeeds_ends_a_goal(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    was_in = sim.cell
    assert sim.teleport(200, 200, 90.0)[0]
    result = goal.result
    assert (result.success, result.reason) == (False, "teleported")
    assert result.message == "The turtle was teleported on the way to cell (120, 8)."
    assert (result.cell_x, result.cell_y) == was_in  # where the goal ended, not where it went
    assert sim.goal is None and sim.cell == (200, 200) and standing(sim)
    wait(sim, 1.0)
    assert sim.cell == (200, 200)


def test_a_teleport_that_is_declined_ends_nothing(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    assert not sim.teleport(-1, 5, 0.0)[0]
    assert not sim.teleport(0, 100, 0.0)[0]
    assert not sim.teleport(50, 50, math.nan)[0]
    assert goal.result is None and sim.goal is goal
    run(sim, goal)
    assert goal.result.reason == "reached"


# --- reset ------------------------------------------------------------------


def test_reset_ends_a_goal(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    was_in, driven = sim.cell, sim.drive.odometer
    assert sim.reset()[0]
    result = goal.result
    assert (result.success, result.reason) == (False, "reset")
    assert result.message == "The simulator was reset on the way to cell (120, 8)."
    assert (result.cell_x, result.cell_y) == was_in
    assert result.distance_m == pytest.approx(driven)
    assert sim.goal is None and sim.cell == (8, 8) and sim.plan == ()
    wait(sim, 1.0)
    assert sim.cell == (8, 8) and standing(sim)


def test_reset_ends_a_dock(sim):
    sim.battery.percent = 5.0
    goal = sim.dock(100.0)
    wait(sim, 1.0)
    sim.reset()
    assert goal.result.reason == "reset"
    assert goal.result.message == "The simulator was reset while the turtle charged on the pad."


# --- canceled ---------------------------------------------------------------


def test_a_cancel_brakes_the_turtle_and_ends_the_goal_once_it_stands(sim):
    goal = driving(sim, sim.drive_to(120, 8))
    assert sim.drive.linear == pytest.approx(c.LINEAR_SPEED_LIMIT)
    goal.cancel()
    speeds = []
    seconds = 0.0
    while goal.result is None:
        sim.step()
        seconds += DT
        speeds.append(sim.drive.linear)
        assert seconds < 2.0
    # Under its acceleration limit: half a second from full speed, no faster.
    assert seconds == pytest.approx(c.LINEAR_SPEED_LIMIT / c.LINEAR_ACCELERATION_LIMIT, abs=2 * DT)
    assert all(
        earlier - later <= c.LINEAR_ACCELERATION_LIMIT * DT + 1e-9
        for earlier, later in itertools.pairwise(speeds)
    )
    result = goal.result
    assert (result.success, result.reason) == (False, "canceled")
    assert result.message == "The goal was cancelled on the way to cell (120, 8)."
    assert standing(sim) and sim.goal is None and sim.mode == "idle"
    wait(sim, 1.0)
    assert (result.cell_x, result.cell_y) == sim.cell  # it stood, and stands


@pytest.mark.parametrize("after", [0.0, 0.02, 0.3, 0.7, 1.5, 4.0, 7.0, 9.3])
def test_a_cancel_at_any_moment_ends_within_two_seconds_with_the_turtle_standing(kinds, after):
    kinds[20:120, 60:62] = c.WALL  # a wall to drive round: turns, legs, waypoints
    sim = Simulation(World(kinds))
    assert sim.teleport(30, 60, 180.0)[0]
    goal = sim.drive_to(90, 60)
    wait(sim, after)
    assert goal.result is None
    goal.cancel()
    seconds = run(sim, goal, seconds=5.0)
    assert goal.result.reason == "canceled"
    assert seconds < 2.0 and standing(sim)


def test_a_cancel_ends_a_crawling_turtle_as_quickly(sim):
    assert sim.teleport(100, 100, 0.0)[0]
    sim.battery.percent = 0.0
    goal = sim.drive_to(100, 160)
    wait(sim, 2.0)
    assert sim.status().crawling
    goal.cancel()
    assert run(sim, goal, seconds=5.0) < 2.0
    assert goal.result.reason == "canceled" and standing(sim)


def test_a_cancel_ends_a_dock_that_charges_with_the_next_step(sim):
    sim.battery.percent = 5.0
    goal = sim.dock(100.0)
    wait(sim, 1.0)
    goal.cancel()
    assert run(sim, goal) == DT
    assert goal.result.reason == "canceled"
    assert goal.result.message == "The goal was cancelled while the turtle charged on the pad."


def test_cancelling_a_goal_that_has_ended_changes_nothing(sim):
    goal = sim.drive_to(999, 0)
    result = goal.result
    goal.cancel()
    wait(sim, 0.1)
    assert goal.result is result


# --- Feedback ---------------------------------------------------------------


def test_the_feedback_of_a_drive_counts_down_and_the_progress_up(sim):
    goal = sim.drive_to(120, 8)
    first = goal.feedback()
    assert first == DriveFeedback(
        phase="planning", cell_x=8, cell_y=8, remaining_m=pytest.approx(11.2), progress=0.0
    )
    seen = []
    while goal.result is None:
        sim.step()
        seen.append(goal.feedback())
    assert {feedback.phase for feedback in seen[:-1]} == {"driving"}
    remaining = [feedback.remaining_m for feedback in seen]
    progress = [feedback.progress for feedback in seen]
    assert all(b <= a + 1e-9 for a, b in itertools.pairwise(remaining))
    assert all(b >= a - 1e-9 for a, b in itertools.pairwise(progress))
    assert remaining[0] == pytest.approx(11.2, abs=0.001) and remaining[-1] < c.GOAL_TOLERANCE_M
    assert progress[0] < 0.001 and progress[-1] == pytest.approx(1.0, abs=0.01)
    half = next(feedback for feedback in seen if feedback.cell_x == 64)
    assert half.progress == pytest.approx(0.5, abs=0.01) and half.cell_y == 8
    assert all(type(feedback.progress) is float for feedback in seen)


def test_the_feedback_of_a_dock_names_the_battery_and_the_way_left(sim):
    assert sim.teleport(60, 8, 180.0)[0]
    sim.battery.percent = 20.0
    goal = sim.dock(60.0)
    wait(sim, 4.0)
    on_the_way = goal.feedback()
    assert on_the_way.phase == "driving" and 3.0 < on_the_way.remaining_m < 4.5
    assert on_the_way.battery_percent < 20.0
    while goal.feedback().phase == "driving":
        sim.step()
    wait(sim, 5.0)
    charging = goal.feedback()
    assert charging.phase == "charging" and charging.remaining_m == 0.0
    assert charging.battery_percent == pytest.approx(sim.battery.percent)
    assert goal.result is None and 20.0 < sim.battery.percent < 60.0


# --- The mode and the path --------------------------------------------------


def test_the_mode_is_auto_while_a_drive_runs_and_docking_while_a_dock_does(sim):
    assert sim.mode == "idle"
    drive = sim.drive_to(120, 8)
    assert sim.mode == "auto" and sim.status().mode == "auto"
    wait(sim, 2.0)
    assert sim.mode == "auto"
    dock = sim.dock(100.0)
    assert drive.result.reason == "preempted" and sim.mode == "docking"
    run(sim, dock)
    assert sim.mode == "idle"


def test_a_goal_ranks_above_teleop_and_charging(sim):
    sim.battery.percent = 50.0
    sim.command(0.2, 0.0)
    sim.step()
    assert sim.mode == "teleop"
    goal = sim.dock(100.0)
    assert sim.mode == "docking"
    run(sim, goal)
    assert sim.mode == "idle"  # full, on the pad


def test_the_path_is_there_while_a_goal_drives_and_empty_without_one(sim):
    assert sim.plan == () and sim.plan_version == 0
    goal = sim.drive_to(40, 24)
    assert sim.plan == ()  # no path before the first step has planned one
    sim.step()
    assert sim.plan == (pytest.approx((0.85, 0.85)), pytest.approx(cell_centre(40, 24)))
    assert sim.plan_version == 1
    wait(sim, 2.0)
    assert sim.plan_version == 1  # the path as planned: it does not shrink
    run(sim, goal)
    assert sim.plan == () and sim.plan_version == 2


def test_a_goal_that_ends_takes_its_path_with_it_whatever_ended_it(sim):
    for end in (
        lambda goal: goal.cancel(),
        lambda goal: sim.command(0.1, 0.0),
        lambda goal: sim.teleport(100, 100, 0.0),
        lambda goal: sim.reset(),
        lambda goal: sim.drive_to(-1, -1),
    ):
        goal = sim.drive_to(150, 150)
        wait(sim, 2.0)
        assert len(sim.plan) >= 2
        end(goal)
        wait(sim, 1.0)
        assert goal.result is not None and sim.plan == () and sim.goal is None
        sim.reset()


# --- Every goal ends --------------------------------------------------------


def test_the_same_goals_give_the_same_run_twice(kinds):
    def play():
        sim = Simulation(World(kinds.copy()))
        results = []
        for start in (
            lambda: sim.drive_to(90, 40),
            lambda: sim.drive_to_square("D5"),
            lambda: sim.dock(100.0),
        ):
            goal = start()
            run(sim, goal)
            results.append(goal.result)
        return results, sim.drive.x, sim.drive.y, sim.drive.heading, sim.battery.percent

    assert play() == play()


def test_a_drive_on_an_empty_battery_still_arrives(sim):
    assert sim.teleport(100, 100, 0.0)[0]
    sim.battery.percent = 0.0
    goal = sim.drive_to(110, 100)
    seconds = run(sim, goal)
    assert goal.result.reason == "reached" and sim.status().crawling
    assert seconds > 1.0 / (c.LINEAR_SPEED_LIMIT * c.CRAWL_LINEAR_FACTOR)

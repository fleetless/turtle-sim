# SPDX-License-Identifier: Apache-2.0
"""The simulation as a whole: steps, the mode, the status, and what the services do."""

import math

import numpy as np
import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.simulation import Simulation
from fleetless_turtle_sim.core.world import World, cell_centre


def run(sim: Simulation, seconds: float, linear: float = 0.0, angular: float = 0.0) -> None:
    """Hold a command for a while, as a publisher at 50 Hz would."""
    for _ in range(round(seconds / sim.step_s)):
        sim.command(linear, angular)
        sim.step()


def wait(sim: Simulation, seconds: float) -> None:
    for _ in range(round(seconds / sim.step_s)):
        sim.step()


@pytest.fixture
def sim(world):
    return Simulation(world)


# --- Start and time ---------------------------------------------------------


def test_it_starts_on_the_pad_heading_east_with_a_full_battery(sim):
    status = sim.status()
    assert (status.cell_x, status.cell_y, status.square) == (8, 8, "A1")
    assert status.heading_deg == 0.0
    assert (status.speed, status.turn_rate) == (0.0, 0.0)
    assert status.mode == "idle"
    assert status.on_charger and not status.crawling and not status.bumper
    assert (status.collisions, status.odometer_m, status.trail) == (0, 0.0, False)
    assert sim.battery.percent == 100.0
    assert (sim.drive.x, sim.drive.y) == pytest.approx((0.85, 0.85))


def test_the_status_holds_plain_python_values(sim):
    # A ROS message takes a bool, an int, a float and a str, and nothing that
    # only looks like one: numpy's own bool is refused.
    sim.set_trail(True)
    for _ in range(10):
        sim.command(0.5, 1.0)
        sim.step()
    expected = {
        "cell_x": int,
        "cell_y": int,
        "square": str,
        "heading_deg": float,
        "speed": float,
        "turn_rate": float,
        "mode": str,
        "crawling": bool,
        "on_charger": bool,
        "bumper": bool,
        "collisions": int,
        "odometer_m": float,
        "trail": bool,
    }
    status = sim.status()
    assert {name: type(getattr(status, name)) for name in expected} == expected
    assert type(sim.battery.percent) is float and type(sim.battery.voltage) is float
    assert all(type(value) is float for value in sim.scan())
    assert all(type(value) is float for value in sim.ranges().values())


def test_a_step_is_one_fixed_step(sim):
    assert sim.step() is None
    assert sim.time == c.STEP_S
    wait(sim, 1.0)
    assert sim.time == pytest.approx(1.0 + c.STEP_S)


def test_the_step_is_an_argument(world):
    coarse = Simulation(world, step_s=0.1)
    for _ in range(10):
        coarse.command(0.5, 0.0)
        coarse.step()
    assert coarse.time == pytest.approx(1.0)
    assert coarse.drive.linear == pytest.approx(0.5)


def test_the_same_commands_give_the_same_run_twice(kinds):
    def drive_about():
        sim = Simulation(World(kinds.copy()))
        run(sim, 2.0, linear=0.5, angular=0.3)
        run(sim, 1.5, linear=-0.2, angular=-1.0)
        wait(sim, 1.0)
        return sim.drive.x, sim.drive.y, sim.drive.heading, sim.battery.percent, sim.status()

    assert drive_about() == drive_about()


def test_driving_moves_the_turtle_and_the_status_follows(sim):
    run(sim, 4.0, linear=0.5)
    status = sim.status()
    # 0.13 m while speeding up, then 3.5 s at full speed: 1.88 m east of 0.85 m.
    assert sim.drive.x == pytest.approx(2.73)
    assert (status.cell_x, status.cell_y, status.square) == (27, 8, "B1")
    assert status.speed == pytest.approx(0.5)
    assert status.odometer_m == pytest.approx(1.88)
    assert not status.on_charger


def test_the_heading_is_reported_from_0_to_360(sim):
    run(sim, 1.0, angular=-1.0)
    assert 270.0 < sim.status().heading_deg < 360.0
    assert sim.status().turn_rate == pytest.approx(-1.0)


@pytest.mark.parametrize("heading", [-1e-17, -1e-12, -1e-9, -2.6e-7, -1e-6, -1e-4, 0.0, 1e-9])
def test_a_hair_below_a_full_turn_the_heading_stays_below_360(sim, heading):
    # The message carries a float32, and from 359.99998 on the nearest one is 360.0.
    sim.drive.heading = heading
    reported = sim.status().heading_deg
    assert 0.0 <= reported < 360.0
    assert 0.0 <= float(np.float32(reported)) < 360.0
    # East, give or take the hair: never a jump elsewhere.
    assert min(reported, 360.0 - reported) < 0.01


def test_every_heading_is_reported_inside_one_turn_as_a_float32(sim):
    for step in range(-7200, 7201):
        sim.drive.heading = math.radians(step / 10.0)
        assert 0.0 <= float(np.float32(sim.status().heading_deg)) < 360.0


# --- The mode ---------------------------------------------------------------


def test_teleop_while_a_non_zero_command_is_at_most_half_a_second_old(sim):
    sim.command(0.2, 0.0)
    sim.step()
    assert sim.mode == "teleop"
    wait(sim, 0.46)
    assert sim.mode == "teleop"
    wait(sim, 0.04)
    assert sim.mode == "idle"


def test_a_zero_command_is_nobody_at_the_controls(sim):
    run(sim, 1.0, linear=0.0, angular=0.0)
    assert sim.mode == "idle"
    run(sim, 0.2, linear=math.nan, angular=math.inf)
    assert sim.mode == "idle"  # what is no number counted as zero
    assert sim.drive.non_finite_commands == 10


def test_a_turn_alone_is_teleop_too(sim):
    run(sim, 0.2, angular=0.5)
    assert sim.mode == "teleop"


def test_charging_on_the_pad_below_full_and_idle_once_full(sim):
    sim.battery.percent = 50.0
    sim.step()
    assert sim.mode == "charging"
    assert sim.battery.supply(sim.on_charger) == "charging"
    wait(sim, 31.0)
    assert sim.battery.percent == 100.0
    assert sim.mode == "idle"
    assert sim.battery.supply(sim.on_charger) == "full"


def test_teleop_wins_over_charging(sim):
    sim.battery.percent = 50.0
    run(sim, 0.2, angular=0.5)
    assert sim.on_charger and sim.mode == "teleop"


def test_off_the_pad_the_turtle_is_idle_and_discharging(sim):
    assert sim.teleport(100, 100, 0.0)[0]
    wait(sim, 10.0)
    assert sim.mode == "idle" and not sim.on_charger
    assert sim.battery.percent == pytest.approx(100 - 10 * 100 / 3600)
    assert sim.battery.supply(sim.on_charger) == "discharging"


# --- The battery and the drive ----------------------------------------------


def test_the_battery_pays_for_driving(world):
    sim = Simulation(world)
    assert sim.teleport(100, 100, 0.0)[0]
    run(sim, 10.0, linear=0.5)
    # Half a second of speeding up costs a little less than full speed would.
    spent = 100.0 - sim.battery.percent
    assert 9.5 * 100 / 300 < spent < 10 * 100 / 300


def test_the_drain_factor_reaches_the_battery(world):
    sim = Simulation(world, battery_drain=0.0)
    assert sim.teleport(100, 100, 0.0)[0]
    run(sim, 60.0, linear=0.5, angular=2.0)
    assert sim.battery.percent == 100.0
    with pytest.raises(ValueError):
        Simulation(world, battery_drain=-1.0)


def test_an_empty_battery_makes_the_turtle_crawl_and_the_pad_ends_it(world):
    sim = Simulation(world)
    assert sim.teleport(30, 8, 180.0)[0]  # a little east of the pad, facing it
    sim.battery.percent = 0.01
    run(sim, 3.0, linear=0.5)
    status = sim.status()
    assert status.crawling and sim.battery.percent == 0.0
    assert status.speed == pytest.approx(0.05)
    # Crawling to the pad: its edge is 1.55 m away, at 0.05 m/s.
    seconds = 0.0
    while not sim.on_charger:
        run(sim, 1.0, linear=0.5)
        seconds += 1.0
        assert seconds < 40.0
    assert seconds > 20.0 and sim.status().crawling
    # Three seconds on the pad are 5 %: the crawl is over and full speed is back.
    wait(sim, 3.5)
    assert not sim.status().crawling and sim.mode == "charging"
    run(sim, 0.5, linear=-0.5)
    assert sim.status().speed < -0.3


# --- reset ------------------------------------------------------------------


def test_reset_puts_everything_back(sim):
    assert sim.set_trail(True)[0]
    run(sim, 3.0, linear=0.5, angular=0.5)
    assert sim.set_block(100, 100, True)[0]
    sim.battery.percent = 20.0
    version = sim.world_version
    ok, message = sim.reset()
    assert (
        ok
        and message == "The turtle is back on the pad, the battery is full, the map is as loaded."
    )
    status = sim.status()
    assert (status.cell_x, status.cell_y, status.heading_deg) == (8, 8, 0.0)
    assert (status.speed, status.turn_rate) == (0.0, 0.0)
    assert (status.collisions, status.odometer_m, status.trail) == (0, 0.0, False)
    assert sim.trail == []
    assert sim.battery.percent == 100.0
    assert sim.world.kind(100, 100) == c.FREE
    assert sim.world_version == version + 1
    wait(sim, 1.0)
    assert sim.status().speed == 0.0  # the command from before is gone


def test_reset_of_an_untouched_world_does_not_announce_a_change(sim):
    version = sim.world_version
    assert sim.reset()[0]
    assert sim.world_version == version


def test_reset_ends_crawling(sim):
    assert sim.teleport(100, 100, 0.0)[0]
    sim.battery.percent = 0.0
    sim.step()
    assert sim.status().crawling
    sim.reset()
    assert not sim.status().crawling


# --- teleport ---------------------------------------------------------------


def test_teleport_puts_the_turtle_on_a_cells_centre_standing(sim):
    run(sim, 1.0, linear=0.5)
    ok, message = sim.teleport(120, 45, 90.0)
    assert ok and message == "The turtle is on cell (120, 45), heading 90 degrees."
    assert (sim.drive.x, sim.drive.y) == pytest.approx(cell_centre(120, 45))
    status = sim.status()
    assert (status.cell_x, status.cell_y, status.square) == (120, 45, "H3")
    assert status.heading_deg == pytest.approx(90.0)
    assert status.speed == 0.0
    wait(sim, 1.0)
    assert (sim.drive.x, sim.drive.y) == pytest.approx(cell_centre(120, 45))


def test_teleport_keeps_the_counters_and_the_battery(sim):
    run(sim, 2.0, linear=0.5)
    odometer, percent = sim.status().odometer_m, sim.battery.percent
    assert sim.teleport(50, 50, 0.0)[0]
    assert sim.status().odometer_m == odometer and sim.battery.percent == percent


@pytest.mark.parametrize(("given", "reported"), [(360.0, 0.0), (-90.0, 270.0), (725.0, 5.0)])
def test_a_heading_is_taken_modulo_a_full_turn(sim, given, reported):
    assert sim.teleport(50, 50, given)[0]
    assert sim.status().heading_deg == pytest.approx(reported)


@pytest.mark.parametrize(("x", "y"), [(-1, 5), (256, 5), (5, -1), (5, 256), (1000, 1000)])
def test_teleport_outside_the_grid_is_declined(sim, x, y):
    before = (sim.drive.x, sim.drive.y)
    ok, message = sim.teleport(x, y, 0.0)
    assert not ok
    assert message == f"Cell ({x}, {y}) is outside the grid, 0 to 255. Teleport declined."
    assert (sim.drive.x, sim.drive.y) == before


def test_teleport_into_an_obstacle_is_declined_naming_it(kinds):
    kinds[40, 12] = c.ROCK
    kinds[41, 12] = c.WALL
    kinds[42, 12] = c.WATER
    sim = Simulation(World(kinds))
    assert sim.teleport(12, 40, 0.0) == (False, "Cell (12, 40) is a rock. Teleport declined.")
    assert sim.teleport(12, 41, 0.0) == (False, "Cell (12, 41) is a wall. Teleport declined.")
    assert sim.teleport(12, 42, 0.0) == (False, "Cell (12, 42) is water. Teleport declined.")
    assert sim.status().cell_x == 8


def test_teleport_next_to_an_obstacle_is_declined(kinds):
    kinds[40, 12] = c.ROCK
    sim = Simulation(World(kinds))
    assert sim.teleport(13, 41, 0.0) == (
        False,
        "The turtle does not fit on cell (13, 41). Teleport declined.",
    )
    assert (
        sim.teleport(0, 100, 0.0)[1]
        == "The turtle does not fit on cell (0, 100). Teleport declined."
    )
    assert sim.teleport(14, 42, 0.0)[0]


def test_teleport_with_a_heading_that_is_no_number_is_declined(sim):
    ok, message = sim.teleport(50, 50, math.nan)
    assert not ok and message == "A heading of nan is not an angle. Teleport declined."


# --- set_block --------------------------------------------------------------


def test_set_block_puts_a_rock_and_frees_a_cell_whatever_it_was(kinds):
    kinds[60, 60] = c.WALL
    kinds[61, 60] = c.WATER
    sim = Simulation(World(kinds))
    assert sim.set_block(100, 100, True) == (True, "Cell (100, 100) is a rock now.")
    assert sim.world.kind(100, 100) == c.ROCK and sim.world.blocks(100, 100)
    assert sim.set_block(60, 60, True)[0] and sim.world.kind(60, 60) == c.ROCK
    assert sim.set_block(60, 61, False) == (True, "Cell (60, 61) is free now.")
    assert sim.world.kind(60, 61) == c.FREE
    assert sim.set_block(100, 100, False)[0] and not sim.world.blocks(100, 100)


def test_set_block_announces_a_change_only_when_there_is_one(sim):
    assert sim.world_version == 0
    assert sim.set_block(100, 100, True)[0]
    assert sim.world_version == 1
    assert sim.set_block(100, 100, True)[0]  # a rock already
    assert sim.world_version == 1
    assert sim.set_block(101, 100, False)[0]  # free already
    assert sim.world_version == 1
    assert sim.set_block(100, 100, False)[0]
    assert sim.world_version == 2


def test_a_new_rock_stops_the_turtle_and_shows_in_the_scan(sim):
    assert sim.teleport(100, 100, 0.0)[0]
    assert sim.set_block(110, 100, True)[0]  # its west face is 0.95 m ahead
    assert sim.ranges()["front"] == pytest.approx(0.95)
    assert sim.scan()[36] == pytest.approx(0.95)
    run(sim, 5.0, linear=0.5)
    assert sim.status().bumper and sim.status().collisions == 1
    assert sim.drive.x < 11.0 - c.TURTLE_RADIUS_M + 1e-6


@pytest.mark.parametrize("occupied", [True, False])
def test_set_block_on_the_pad_is_refused(sim, occupied):
    assert sim.set_block(5, 5, occupied) == (
        False,
        "Cell (5, 5) is part of the charging pad. Nothing changed.",
    )
    assert sim.world.kind(5, 5) == c.PAD and sim.world_version == 0


@pytest.mark.parametrize(("x", "y"), [(-1, 0), (0, 256), (256, 256)])
def test_set_block_outside_the_grid_is_refused(sim, x, y):
    assert sim.set_block(x, y, True) == (
        False,
        f"Cell ({x}, {y}) is outside the grid, 0 to 255. Nothing changed.",
    )


def test_set_block_under_the_turtle_is_refused(sim):
    assert sim.teleport(100, 100, 0.0)[0]
    for x, y in [(100, 100), (101, 100), (99, 99), (101, 101)]:
        assert sim.set_block(x, y, True) == (
            False,
            f"The turtle's footprint touches cell ({x}, {y}). Nothing changed.",
        )
        assert sim.world.kind(x, y) == c.FREE
    assert sim.set_block(102, 100, True)[0]  # resting against the footprint is not under it
    assert sim.world.fits(sim.drive.x, sim.drive.y)


# --- set_trail --------------------------------------------------------------


def test_the_trail_is_off_until_asked_for(sim):
    run(sim, 2.0, linear=0.5)
    assert sim.trail == [] and not sim.status().trail


def test_the_trail_holds_a_point_every_tenth_of_a_metre(sim):
    assert sim.set_trail(True) == (True, "The trail is being recorded.")
    assert sim.status().trail
    assert sim.trail == [pytest.approx((0.85, 0.85))]
    run(sim, 4.0, linear=0.5)  # 1.88 m
    assert len(sim.trail) == 19
    gaps = [math.dist(a, b) for a, b in zip(sim.trail, sim.trail[1:], strict=False)]
    assert all(0.1 - 1e-9 <= gap < 0.1 + 0.011 for gap in gaps)
    assert sim.trail[-1][0] <= sim.drive.x


def test_standing_adds_nothing_to_the_trail(sim):
    sim.set_trail(True)
    version = sim.trail_version
    wait(sim, 5.0)
    assert len(sim.trail) == 1 and sim.trail_version == version


def test_switching_the_trail_on_twice_keeps_it(sim):
    sim.set_trail(True)
    run(sim, 2.0, linear=0.5)
    points = list(sim.trail)
    assert sim.set_trail(True)[0]
    assert sim.trail == points


def test_switching_the_trail_off_clears_it(sim):
    sim.set_trail(True)
    run(sim, 2.0, linear=0.5)
    version = sim.trail_version
    assert sim.set_trail(False) == (True, "The trail is off and empty.")
    assert sim.trail == [] and not sim.status().trail
    assert sim.trail_version > version
    run(sim, 1.0, linear=0.5)
    assert sim.trail == []


def test_the_trail_keeps_the_newest_2000_points(world):
    sim = Simulation(world, battery_drain=0.0)
    assert sim.teleport(20, 100, 0.0)[0]
    sim.set_trail(True)
    # Circles of radius 2.5 m: 250 m driven, 2500 points' worth.
    run(sim, 500.0, linear=0.5, angular=0.2)
    assert len(sim.trail) == c.TRAIL_MAX_POINTS
    assert math.dist(sim.trail[-1], (sim.drive.x, sim.drive.y)) < 0.1

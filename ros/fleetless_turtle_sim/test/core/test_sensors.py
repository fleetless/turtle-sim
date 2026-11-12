# SPDX-License-Identifier: Apache-2.0
"""The laser and the range sensors, against distances known from a hand-made map."""

import math

import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.sensors import cast, ranges, scan
from fleetless_turtle_sim.core.world import World, cell_centre

EAST, NORTH, WEST, SOUTH = 0.0, math.pi / 2, math.pi, -math.pi / 2


def ray_index(degrees: float) -> int:
    """The scan's ray that points `degrees` from the heading."""
    return round((math.radians(degrees) - c.LASER_ANGLE_MIN) / c.LASER_ANGLE_INCREMENT)


@pytest.fixture
def walled(kinds):
    """A wall one cell thick at x = 6.0 to 6.1 m, from south to north."""
    kinds[:, 60] = c.WALL
    return World(kinds)


# --- One ray ----------------------------------------------------------------


def test_a_ray_ends_at_the_face_of_the_first_blocking_cell(walled):
    x, y = cell_centre(50, 50)  # 5.05 m, 5.05 m
    assert cast(walled, x, y, EAST, 4.0) == pytest.approx(0.95)
    assert cast(walled, x, y, math.radians(45), 4.0) == pytest.approx(
        0.95 / math.cos(math.radians(45))
    )
    assert cast(walled, x, y, math.radians(-30), 4.0) == pytest.approx(
        0.95 / math.cos(math.radians(30))
    )


def test_a_ray_from_the_other_side_ends_at_the_other_face(walled):
    x, y = cell_centre(70, 50)  # 7.05 m
    assert cast(walled, x, y, WEST, 4.0) == pytest.approx(0.95)


@pytest.mark.parametrize("kind", [c.WALL, c.ROCK, c.WATER])
def test_wall_rock_and_water_stop_a_ray(kinds, kind):
    kinds[50, 60] = kind
    world = World(kinds)
    assert cast(world, *cell_centre(50, 50), EAST, 4.0) == pytest.approx(0.95)


def test_the_pad_stops_nothing(world):
    # From the middle of the pad, the pad's own cells are in the way of nothing.
    assert cast(world, *cell_centre(8, 8), EAST, 2.0) is None


def test_a_ray_finds_a_single_cell_and_misses_it_by_a_hair(kinds):
    kinds[70, 50] = c.ROCK  # 5.0 to 5.1 m east, 7.0 to 7.1 m north
    world = World(kinds)
    assert cast(world, 5.05, 5.05, NORTH, 4.0) == pytest.approx(1.95)
    assert cast(world, 5.101, 5.05, NORTH, 4.0) is None
    assert cast(world, 4.999, 5.05, NORTH, 4.0) is None


def test_the_grids_edge_ends_a_ray(world):
    assert cast(world, 0.55, 5.05, WEST, 4.0) == pytest.approx(0.55)
    assert cast(world, 5.05, 24.05, NORTH, 4.0) == pytest.approx(1.55)
    assert cast(world, 5.05, 0.35, SOUTH, 4.0) == pytest.approx(0.35)
    assert cast(world, 25.05, 5.05, EAST, 4.0) == pytest.approx(0.55)


def test_beyond_its_range_a_ray_hits_nothing(walled):
    assert cast(walled, 2.05, 5.05, EAST, 4.0) == pytest.approx(3.95)
    assert cast(walled, 1.95, 5.05, EAST, 4.0) is None
    assert cast(walled, 5.05, 5.05, NORTH, 4.0) is None


def test_a_ray_that_starts_in_a_blocking_cell_is_there_already(walled):
    assert cast(walled, 6.05, 5.05, EAST, 4.0) == 0.0


# --- The laser --------------------------------------------------------------


def test_the_scan_has_72_rays_from_minus_pi_counter_clockwise(walled):
    x, y = cell_centre(50, 50)
    readings = scan(walled, x, y, EAST)
    assert len(readings) == c.LASER_RAYS == 72
    assert ray_index(-180) == 0 and ray_index(0) == 36 and ray_index(175) == 71
    assert readings[ray_index(0)] == pytest.approx(0.95)
    assert readings[ray_index(45)] == pytest.approx(0.95 / math.cos(math.radians(45)))
    assert readings[ray_index(-45)] == pytest.approx(0.95 / math.cos(math.radians(45)))
    # The wall runs from south to north: every ray within 75 degrees of east finds it.
    for degrees in range(-75, 80, 5):
        expected = 0.95 / math.cos(math.radians(degrees))
        assert readings[ray_index(degrees)] == pytest.approx(expected), degrees


def test_a_ray_that_hits_nothing_is_infinite(walled):
    readings = scan(walled, *cell_centre(50, 50), EAST)
    assert readings[ray_index(-180)] == math.inf  # west: open for 5 m
    assert readings[ray_index(90)] == math.inf
    assert readings[ray_index(80)] == math.inf  # the wall, but 5.5 m away along this ray
    assert all(r == math.inf or c.LASER_RANGE_MIN_M <= r <= c.LASER_RANGE_MAX_M for r in readings)


def test_the_scan_turns_with_the_turtle(walled):
    x, y = cell_centre(50, 50)
    facing_north = scan(walled, x, y, NORTH)
    assert facing_north[ray_index(-90)] == pytest.approx(0.95)  # the wall is to its right now
    assert facing_north[ray_index(0)] == math.inf
    facing_west = scan(walled, x, y, WEST)
    assert facing_west[ray_index(-180)] == pytest.approx(0.95)  # and now behind it


def test_the_scan_reads_no_less_than_its_minimum(walled):
    # Resting against the wall, the nearest face is a footprint radius away.
    readings = scan(walled, 5.9, 5.05, EAST)
    assert readings[ray_index(0)] == pytest.approx(0.1)
    assert min(readings) >= c.LASER_RANGE_MIN_M


# --- The range sensors ------------------------------------------------------


def test_front_left_and_right(kinds):
    kinds[:, 60] = c.WALL  # east of the turtle
    kinds[70, :] = c.ROCK  # north of it: y = 7.0 to 7.1 m
    world = World(kinds)
    x, y = cell_centre(50, 50)
    assert ranges(world, x, y, EAST) == pytest.approx({"front": 0.95, "left": 1.95, "right": 4.0})
    assert ranges(world, x, y, NORTH) == pytest.approx({"front": 1.95, "left": 4.0, "right": 0.95})
    assert ranges(world, x, y, WEST) == pytest.approx({"front": 4.0, "left": 4.0, "right": 1.95})


def test_nothing_in_range_reports_the_maximum_as_a_number(world):
    readings = ranges(world, 12.05, 12.05, EAST)
    assert readings == {"front": c.RANGE_MAX_M, "left": c.RANGE_MAX_M, "right": c.RANGE_MAX_M}
    assert all(math.isfinite(value) for value in readings.values())


def test_a_range_reads_no_less_than_its_minimum(walled):
    assert ranges(walled, 5.9, 5.05, EAST)["front"] == pytest.approx(c.RANGE_MIN_M)

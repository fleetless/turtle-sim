# SPDX-License-Identifier: Apache-2.0
"""The spec's table of constants, row by row.

A number that changes in constants.py has to change here as well, on purpose.
"""

import math

from fleetless_turtle_sim.core import constants as c


def test_the_grid():
    assert c.GRID_CELLS == 256
    assert c.CELL_SIZE_M == 0.1
    assert c.GRID_CELLS * c.CELL_SIZE_M == 25.6
    assert (c.SQUARE_CELLS, c.SQUARES) == (16, 16)


def test_the_kinds_are_the_world_messages():
    assert (c.FREE, c.WALL, c.ROCK, c.WATER, c.PAD) == (0, 1, 2, 3, 4)
    assert c.BLOCKING == (c.WALL, c.ROCK, c.WATER)
    assert c.MAP_COLOURS == {
        (255, 255, 255): c.FREE,
        (0, 0, 0): c.WALL,
        (128, 128, 128): c.ROCK,
        (0, 0, 255): c.WATER,
        (0, 255, 0): c.PAD,
    }


def test_the_turtle():
    assert c.TURTLE_RADIUS_M == 0.1
    assert c.STEP_S == 0.02
    assert c.LINEAR_SPEED_LIMIT == 0.5
    assert c.ANGULAR_SPEED_LIMIT == 2.0
    assert c.LINEAR_ACCELERATION_LIMIT == 1.0
    assert c.ANGULAR_ACCELERATION_LIMIT == 6.0
    assert c.COMMAND_TIMEOUT_S == 0.5
    assert c.TRACK_WIDTH_M == 0.16
    assert c.BUMPER_HOLD_S == 0.2


def test_the_sensors():
    assert c.LASER_RAYS == 72
    assert math.isclose(c.LASER_ANGLE_INCREMENT, math.radians(5))
    assert -math.pi == c.LASER_ANGLE_MIN
    assert math.isclose(c.LASER_RAYS * c.LASER_ANGLE_INCREMENT, 2 * math.pi)
    assert (c.LASER_RANGE_MIN_M, c.LASER_RANGE_MAX_M, c.LASER_RATE_HZ) == (0.1, 4.0, 5.0)
    assert {"front": 0.0, "left": math.pi / 2, "right": -math.pi / 2} == c.RANGE_SENSOR_ANGLES
    assert (c.RANGE_MIN_M, c.RANGE_MAX_M, c.RANGE_RATE_HZ) == (0.1, 4.0, 10.0)


def test_the_battery():
    assert c.BATTERY_STANDING_S == 3600
    assert c.BATTERY_DRIVING_S == 300
    assert c.BATTERY_TURNING_S == 600
    assert c.BATTERY_CHARGING_S == 60
    assert (c.CRAWL_START_PERCENT, c.CRAWL_END_PERCENT) == (0, 5)
    assert (c.CRAWL_LINEAR_FACTOR, c.CRAWL_ANGULAR_FACTOR) == (0.1, 0.25)
    assert (c.BATTERY_VOLTAGE_EMPTY, c.BATTERY_VOLTAGE_FULL) == (10.0, 12.6)


def test_navigation():
    assert c.PLANNER_CLEARANCE_M == 0.05
    assert c.GOAL_TOLERANCE_M == 0.03
    assert c.PATH_CHECK_PERIOD_S == 1.0
    assert (c.STUCK_PROGRESS_M, c.STUCK_WINDOW_S) == (0.02, 5.0)


def test_the_trail_and_the_rates():
    assert (c.TRAIL_SPACING_M, c.TRAIL_MAX_POINTS) == (0.1, 2000)
    assert (c.ODOMETRY_RATE_HZ, c.STATUS_RATE_HZ, c.BATTERY_RATE_HZ) == (20.0, 10.0, 1.0)


def test_a_full_speed_step_is_shorter_than_a_cell():
    # What keeps the turtle from tunnelling through a wall one cell thick.
    assert math.isclose(c.LINEAR_SPEED_LIMIT * c.STEP_S, 0.01)
    assert c.LINEAR_SPEED_LIMIT * c.STEP_S < c.CELL_SIZE_M

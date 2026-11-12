# SPDX-License-Identifier: Apache-2.0
"""Every number of the simulation, named, and nowhere else.

The shipped fleetless.yaml repeats a few of them as bounds; a test keeps the
two in step.
"""

import math

# --- The grid ---------------------------------------------------------------

GRID_CELLS = 256
"""Cells per side. The world is GRID_CELLS x GRID_CELLS."""

CELL_SIZE_M = 0.1
"""Edge of one cell, so the world is 25.6 m x 25.6 m."""

SQUARE_CELLS = 16
"""Cells per side of a named square, A1 to P16."""

SQUARES = GRID_CELLS // SQUARE_CELLS
"""Squares per side: sixteen letters, sixteen numbers."""

# Cell kinds. The values are those of fleetless_turtle_msgs/msg/World.
FREE = 0
WALL = 1
ROCK = 2
WATER = 3
PAD = 4

BLOCKING = (WALL, ROCK, WATER)
"""Wall, rock and water differ only in how they look."""

MAP_COLOURS = {
    (0xFF, 0xFF, 0xFF): FREE,
    (0x00, 0x00, 0x00): WALL,
    (0x80, 0x80, 0x80): ROCK,
    (0x00, 0x00, 0xFF): WATER,
    (0x00, 0xFF, 0x00): PAD,
}
"""The five colours of a map file, as (red, green, blue)."""

# --- The turtle -------------------------------------------------------------

TURTLE_RADIUS_M = 0.1
"""The footprint is a circle, two cells wide."""

TOUCH_TOLERANCE_M2 = 1e-9
"""Slack on a squared distance, so a circle that only rests against a cell
does not count as touching it once floating point has had its say."""

STEP_S = 0.02
"""The simulation step, fixed: 50 Hz."""

CATCH_UP_STEPS = 5
"""How many steps a late timer may take at once, a tenth of a second's
worth. A longer lag is not made up: the world stood still."""

TIME_TOLERANCE_S = 1e-9
"""Slack on a time. Times are sums of steps, and "after 0.5 s" must not
depend on how twenty-five times 0.02 happens to round."""

LINEAR_SPEED_LIMIT = 0.5
"""m/s, forwards and backwards."""

ANGULAR_SPEED_LIMIT = 2.0
"""rad/s, either way."""

LINEAR_ACCELERATION_LIMIT = 1.0
"""m/s^2."""

ANGULAR_ACCELERATION_LIMIT = 6.0
"""rad/s^2."""

COMMAND_TIMEOUT_S = 0.5
"""After this long without a velocity command the target speeds are zero.
It is also how long a non-zero command counts as a person at the controls."""

TRACK_WIDTH_M = 0.16
"""Distance between the two sides' wheels. Only the legs use it."""

BUMPER_HOLD_S = 0.2
"""The bumper stays true this long after a refused move."""

# --- Sensors ----------------------------------------------------------------

LASER_RAYS = 72
LASER_ANGLE_MIN = -math.pi
LASER_ANGLE_INCREMENT = math.radians(5.0)
LASER_RANGE_MIN_M = 0.1
LASER_RANGE_MAX_M = 4.0
LASER_RATE_HZ = 5.0

RANGE_SENSOR_ANGLES = {
    "front": 0.0,
    "left": math.pi / 2,
    "right": -math.pi / 2,
}
"""One ray each, as an angle from the heading."""

RANGE_MIN_M = 0.1
RANGE_MAX_M = 4.0
RANGE_RATE_HZ = 10.0

# --- The battery ------------------------------------------------------------

BATTERY_STANDING_S = 3600.0
"""Standing empties a full battery in an hour."""

BATTERY_DRIVING_S = 300.0
"""Full linear speed empties it in five minutes, standing included."""

BATTERY_TURNING_S = 600.0
"""Turning on the spot at full angular speed: ten minutes, standing included."""

BATTERY_CHARGING_S = 60.0
"""On the pad an empty battery is full after a minute."""

CRAWL_START_PERCENT = 0.0
CRAWL_END_PERCENT = 5.0
"""Crawling starts at an empty battery and ends once it is back here."""

CRAWL_LINEAR_FACTOR = 0.1
CRAWL_ANGULAR_FACTOR = 0.25
"""What is left of the speed limits while crawling."""

BATTERY_VOLTAGE_EMPTY = 10.0
BATTERY_VOLTAGE_FULL = 12.6
"""Volts, linear in between."""

# --- Navigation -------------------------------------------------------------

PLANNER_CLEARANCE_M = 0.05
"""A planned path keeps this much room beyond the footprint."""

GOAL_TOLERANCE_M = 0.03
"""A goal is reached this close to the target cell's centre."""

PATH_CHECK_PERIOD_S = 1.0
"""The remaining path is checked this often, and after every set_block."""

STUCK_PROGRESS_M = 0.02
STUCK_WINDOW_S = 5.0
"""Less than this much progress in this long, while a goal drives, is stuck."""

START_SEARCH_CELLS = 3
"""A turtle that stands too close to an obstacle to plan from its own cell
starts at the nearest traversable cell at most this many cells away."""

FACING_TOLERANCE = math.radians(5.0)
"""The turtle turns on the spot until it faces the next waypoint this well,
then drives."""

STEERING_GAIN = 4.0
"""While it drives, the turtle turns towards the waypoint at this many rad/s
per radian it is off."""

HOLD_HEADING_WITHIN_M = 0.05
"""This close to a waypoint the turtle stops steering: the bearing to a point
almost reached swings with every millimetre."""

STOP_WITHIN_M = 0.002
"""This close to a waypoint the turtle stops braking by the book and just
stops. The book alone would have it creep up to the point for ever."""

GOAL_FEEDBACK_RATE_HZ = 2.0
"""How often a running goal reports."""

DOCK_TARGET_MIN_PERCENT = 1.0
DOCK_TARGET_MAX_PERCENT = 100.0
"""What a dock goal may name as the charge to wait for."""

# --- The trail --------------------------------------------------------------

TRAIL_SPACING_M = 0.1
TRAIL_MAX_POINTS = 2000
"""A point every 0.1 m, the newest 2000."""

TRAIL_RATE_HZ = 1.0
"""The trail is published as it grows, and no more often than this. What a
service did to it, emptying it or switching it on, is published at once."""

DISTANCE_TOLERANCE_M = 1e-9
"""Slack on a distance: ten steps of 0.01 m are 0.1 m, whatever their sum rounds to."""

# --- The model's joints -----------------------------------------------------

LEG_STRIDE_M = 0.25
"""A leg swings forwards and back once while its side's wheel rolls this far."""

LEG_SWING = 0.45
"""Radians a leg swings either way from where it rests."""

HEAD_TURN_LIMIT = math.radians(30.0)
"""The head turns into a curve, by this much at the angular speed limit."""

# --- What the node publishes, and how often ---------------------------------

ODOMETRY_RATE_HZ = 20.0
STATUS_RATE_HZ = 10.0
BATTERY_RATE_HZ = 1.0
JOINT_RATE_HZ = 20.0

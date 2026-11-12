# SPDX-License-Identifier: Apache-2.0
"""The laser and the three range sensors: exact ray casting on the grid.

A ray starts at the turtle's centre and ends at the face of the first
blocking cell it enters. The grid's edge is such a face, since everything
outside blocks.
"""

import math

from .constants import (
    CELL_SIZE_M,
    LASER_ANGLE_INCREMENT,
    LASER_ANGLE_MIN,
    LASER_RANGE_MAX_M,
    LASER_RANGE_MIN_M,
    LASER_RAYS,
    RANGE_MAX_M,
    RANGE_MIN_M,
    RANGE_SENSOR_ANGLES,
)
from .world import World, cell_of


def cast(world: World, x: float, y: float, angle: float, max_range: float) -> float | None:
    """Metres from (x, y) along `angle` to the first blocking cell, or None beyond `max_range`.

    The ray walks from cell to cell and always into the one whose border it
    crosses first, so it skips nothing and looks at no cell it does not enter.
    """
    cx, cy = cell_of(x, y)
    if world.blocks(cx, cy):
        return 0.0
    dx, dy = math.cos(angle), math.sin(angle)
    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1
    # How far along the ray the next border to the east or west lies, and how
    # far apart such borders are; the same for north and south.
    if dx != 0.0:
        next_x = ((cx + (step_x > 0)) * CELL_SIZE_M - x) / dx
        every_x = CELL_SIZE_M / abs(dx)
    else:
        next_x = every_x = math.inf
    if dy != 0.0:
        next_y = ((cy + (step_y > 0)) * CELL_SIZE_M - y) / dy
        every_y = CELL_SIZE_M / abs(dy)
    else:
        next_y = every_y = math.inf

    while True:
        if next_x < next_y:
            distance = next_x
            next_x += every_x
            cx += step_x
        else:
            distance = next_y
            next_y += every_y
            cy += step_y
        if distance > max_range:
            return None
        if world.blocks(cx, cy):
            return distance


def scan(world: World, x: float, y: float, heading: float) -> list[float]:
    """The laser: 72 ranges from -pi, counter-clockwise, relative to the heading.

    A ray that hits nothing within range is `inf` (REP 117).
    """
    ranges = []
    for i in range(LASER_RAYS):
        angle = heading + LASER_ANGLE_MIN + i * LASER_ANGLE_INCREMENT
        distance = cast(world, x, y, angle, LASER_RANGE_MAX_M)
        ranges.append(math.inf if distance is None else max(distance, LASER_RANGE_MIN_M))
    return ranges


def ranges(world: World, x: float, y: float, heading: float) -> dict[str, float]:
    """The range sensors, by name. Nothing in range reports the maximum, a number."""
    readings = {}
    for name, offset in RANGE_SENSOR_ANGLES.items():
        distance = cast(world, x, y, heading + offset, RANGE_MAX_M)
        readings[name] = RANGE_MAX_M if distance is None else max(distance, RANGE_MIN_M)
    return readings

# SPDX-License-Identifier: Apache-2.0
"""The `eye` camera: what the turtle sees, by ray casting on the grid.

One ray per column of the picture, 70 degrees from the left edge to the
right, from 0.1 m above the turtle's centre. A ray ends where it enters a
wall, where it leaves the grid, or after 8 m. What it found is drawn as an
upright face at its kind's height and colour, shaded by its distance and by
where it looks. Sky above, floor below.

Two kinds do not end a ray, because the eye sees past them:

- **Water is flat.** It is part of the floor, in its own colour, and the far
  bank stands behind it.
- **A rock is low.** Its face is drawn, and so is the wall behind it, which
  rises over it. A second rock behind the first is hidden: both are as tall.

There is no loop over rays or pixels in here. A ray is a row of an array,
and what happens to one happens to all.
"""

import math
from dataclasses import dataclass

import numpy as np

from fleetless_turtle_sim.core.constants import CELL_SIZE_M, ROCK, WALL

from . import constants as look
from .floor import cell_colours
from .scene import Remembered, Scene

WIDTH, HEIGHT = look.EYE_WIDTH_PX, look.EYE_HEIGHT_PX

FOCAL_PX = (WIDTH / 2) / math.tan(look.EYE_FIELD_OF_VIEW / 2)
"""How many pixels from the picture's middle a thing lies that is as far to
the side as it is ahead."""

# Where a face looks, as an index into FACE_LIGHT.
WEST, SOUTH, EAST, NORTH = range(4)

# What lies past the grid's edge, as a kind of its own.
OUTSIDE = 255

_FACE_COLOURS = np.zeros((256, 3))
_FACE_COLOURS[WALL] = look.WALL
_FACE_COLOURS[ROCK] = look.ROCK
_FACE_COLOURS[OUTSIDE] = look.OUTSIDE
_FACE_LIGHT = np.array(look.FACE_LIGHT)
_HAZE = np.array(look.HAZE, np.float32)

# For every column: how far to the right its ray goes for each metre ahead.
# Distances along a ray are counted in metres ahead, which is what keeps a
# straight wall straight in the picture.
_SIDEWAYS = (np.arange(WIDTH) + 0.5 - WIDTH / 2) / FOCAL_PX

# For every row: how many pixels below the horizon its middle lies. Negative above.
_BELOW = (np.arange(HEIGHT, dtype=np.float32) + 0.5 - HEIGHT / 2)[:, None]

# The floor a row shows lies this far ahead: where a line from the eye
# through that row meets the ground.
_FLOOR_AHEAD_M = FOCAL_PX * look.EYE_HEIGHT_M / _BELOW[HEIGHT // 2 :]

# Enough borders between cells to reach the range along the most slanted ray.
_BORDERS = np.arange(
    int(look.EYE_RANGE_M * math.hypot(1.0, float(np.abs(_SIDEWAYS).max())) / CELL_SIZE_M) + 2
)


def _fog(distance_m: np.ndarray) -> np.ndarray:
    """How much of a colour is haze at a distance: none nearby, all of it at the range."""
    return (np.clip(distance_m / look.EYE_RANGE_M, 0.0, 1.0) ** look.FOG_POWER).astype(np.float32)


# The sky: haze at the horizon, blue overhead. The same in every picture.
_SKY = np.rint(
    _HAZE + (np.array(look.SKY) - _HAZE) * (-_BELOW[: HEIGHT // 2] / (HEIGHT / 2))[..., None]
).astype(np.uint8)

# The floor's haze depends on the row alone: `colour * _FLOOR_KEEPS + _FLOOR_HAZE`.
_FLOOR_KEEPS = (1.0 - _fog(_FLOOR_AHEAD_M))[..., None]
_FLOOR_HAZE = _HAZE * (1.0 - _FLOOR_KEEPS)

_floor = Remembered(cell_colours)
# The cells with one more of OUTSIDE all the way round: a ray that leaves
# the grid finds that kind there, and no index is ever out of range.
_cells = Remembered(lambda kinds: np.pad(kinds, 1, constant_values=OUTSIDE))


def render(scene: Scene) -> np.ndarray:
    """The picture as `[row, column]` of red, green and blue bytes, 320 x 240."""
    ahead_x, ahead_y = math.cos(scene.heading), math.sin(scene.heading)
    # To the right of "ahead" is a quarter turn clockwise.
    dx = ahead_x + _SIDEWAYS * ahead_y
    dy = ahead_y - _SIDEWAYS * ahead_x

    picture = np.empty((HEIGHT, WIDTH, 3), np.uint8)
    picture[: HEIGHT // 2] = _SKY
    picture[HEIGHT // 2 :] = _look_at_the_floor(scene.kinds, scene.x, scene.y, dx, dy)
    walls, rocks = cast(scene.kinds, scene.x, scene.y, dx, dy)
    # Far to near: a rock stands in front of the wall behind it.
    walls.draw(picture, look.WALL_HEIGHT_M)
    rocks.draw(picture, look.ROCK_HEIGHT_M)
    return picture


def _look_at_the_floor(kinds: np.ndarray, x: float, y: float, dx, dy) -> np.ndarray:
    """The lower half of the picture as if nothing stood on the floor."""
    rows, columns = kinds.shape
    cx = ((x + _FLOOR_AHEAD_M * dx) / CELL_SIZE_M).astype(np.intp)
    cy = ((y + _FLOOR_AHEAD_M * dy) / CELL_SIZE_M).astype(np.intp)
    # Past the grid's edge nothing is seen: the edge is a face and hides it.
    np.clip(cx, 0, columns - 1, out=cx)
    np.clip(cy, 0, rows - 1, out=cy)
    return (_floor(kinds)[cy, cx] * _FLOOR_KEEPS + _FLOOR_HAZE).astype(np.uint8)


@dataclass
class Faces:
    """What the rays found of one height: per column, the nearest upright face."""

    distance: np.ndarray
    """Metres ahead; infinite where a ray found none."""
    colour: np.ndarray
    """Red, green and blue, lit, before the haze."""

    @classmethod
    def all_at(cls, count: int, distance_m: float, colour=(0, 0, 0)) -> "Faces":
        return cls(np.full(count, distance_m), np.tile(np.array(colour, float), (count, 1)))

    def rows(self, height_m: float) -> tuple[np.ndarray, np.ndarray]:
        """The rows a face covers, as pixels below the horizon: its top and its foot."""
        distance = np.maximum(self.distance, look.EYE_NEAR_M)
        top = -FOCAL_PX * (height_m - look.EYE_HEIGHT_M) / distance
        foot = FOCAL_PX * look.EYE_HEIGHT_M / distance
        return top.astype(np.float32), foot.astype(np.float32)

    def draw(self, picture: np.ndarray, height_m: float) -> None:
        top, foot = self.rows(height_m)
        covered = (top <= _BELOW) & (foot >= _BELOW)
        fog = _fog(self.distance)[:, None]
        colour = np.rint(self.colour * (1.0 - fog) + _HAZE * fog).astype(np.uint8)
        np.copyto(picture, colour, where=covered[..., None])


def cast(kinds: np.ndarray, x: float, y: float, dx: np.ndarray, dy: np.ndarray):
    """One ray per element of `dx`, `dy` from (x, y): the walls and the rocks they meet.

    A ray is (x, y) + t * (dx, dy), and `t` is the distance it reports. It
    ends at the first wall, at the grid's edge, or at the range.

    A ray enters every cell through a border: one between two columns of
    cells or one between two rows. The borders of each sort lie evenly
    spaced along a ray, so where it crosses them, and which cell lies
    behind each crossing, is arithmetic for all rays and all borders at
    once. Nothing is skipped: the first wall behind a border of either
    sort is the first wall.
    """
    cells = _cells(kinds)
    count = dx.size

    # An eye inside a block sees that block and nothing else. The simulator
    # never puts the turtle there; a picture is drawn all the same.
    rows, columns = kinds.shape
    cx, cy = math.floor(x / CELL_SIZE_M), math.floor(y / CELL_SIZE_M)
    here = kinds[cy, cx] if 0 <= cx < columns and 0 <= cy < rows else OUTSIDE
    if here in (WALL, ROCK, OUTSIDE):
        block = Faces.all_at(count, look.EYE_NEAR_M, _FACE_COLOURS[here])
        nothing = Faces.all_at(count, math.inf)
        return (nothing, block) if here == ROCK else (block, nothing)

    # Through a border between columns, a ray going east meets a face that
    # looks west. The borders between rows are the same thing with x and y
    # exchanged.
    across = _Crossings(cells, x, y, dx, dy, np.where(dx > 0, WEST, EAST))
    along = _Crossings(cells.T, y, x, dy, dx, np.where(dy > 0, SOUTH, NORTH))

    def nearest(kinds_wanted, before=None) -> Faces:
        one, other = across.first(kinds_wanted, before), along.first(kinds_wanted, before)
        nearer = one.distance <= other.distance
        return Faces(
            np.where(nearer, one.distance, other.distance),
            np.where(nearer[:, None], one.colour, other.colour),
        )

    walls = nearest((WALL, OUTSIDE))
    rocks = nearest((ROCK,), before=walls.distance)
    return walls, rocks


class _Crossings:
    """Where every ray crosses the borders between columns of cells, and what lies behind each.

    `distance[ray, border]` is how far along the ray the crossing is,
    `kind[ray, border]` the cell the ray enters there. `cells` is the grid
    with its rim of OUTSIDE.
    """

    def __init__(self, cells, x, y, dx, dy, face):
        rows, columns = cells.shape
        column = math.floor(x / CELL_SIZE_M)
        east = dx > 0
        with np.errstate(divide="ignore", invalid="ignore"):
            # A ray that runs along the columns crosses none of their borders.
            first = np.where(dx != 0, ((column + east) * CELL_SIZE_M - x) / dx, np.inf)
            every = np.where(dx != 0, CELL_SIZE_M / np.abs(dx), 0.0)
        self.distance = first[:, None] + _BORDERS * every[:, None]
        self.in_range = self.distance <= look.EYE_RANGE_M

        # The cell behind each crossing, counted in the rimmed grid: one
        # further along both ways. Beyond the range it is not looked at.
        cx = (column + 1) + np.where(east, 1, -1)[:, None] * (_BORDERS + 1)
        reached = np.minimum(self.distance, look.EYE_RANGE_M)
        cy = np.floor((y + reached * dy[:, None]) / CELL_SIZE_M).astype(np.intp) + 1
        np.clip(cx, 0, columns - 1, out=cx)
        np.clip(cy, 0, rows - 1, out=cy)
        self.kind = cells[cy, cx]
        self._lighter = (cx + cy) % 2 == 0
        self._face_light = _FACE_LIGHT[face]
        self._rays = np.arange(dx.size)

    def first(self, kinds_wanted, before=None) -> Faces:
        """Per ray, the nearest crossing into a cell of one of these kinds."""
        wanted = self.kind == kinds_wanted[0]
        for kind in kinds_wanted[1:]:
            wanted |= self.kind == kind
        wanted &= self.in_range
        if before is not None:
            wanted &= self.distance < before[:, None]
        border = wanted.argmax(axis=1)
        chosen = (self._rays, border)
        distance = np.where(wanted[chosen], self.distance[chosen], np.inf)
        # Every other cell a little lighter, as on the floor.
        contrast = np.where(self._lighter[chosen], look.CELL_CONTRAST, -look.CELL_CONTRAST)
        light = self._face_light * (1.0 + contrast)
        return Faces(distance, _FACE_COLOURS[self.kind[chosen]] * light[:, None])

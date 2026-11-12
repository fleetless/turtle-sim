# SPDX-License-Identifier: Apache-2.0
"""The `top` camera: the map around the turtle, north up.

A window of 12 m x 12 m, centred on the turtle, at four pixels a cell: the
cells by their kind with a hint of height, the lines between the squares,
the pad, and each square's name on top of whatever stands in its corner;
then the trail, the path ahead of the turtle, the target and the turtle.

The world is drawn once, whole, and kept until a cell changes. A picture is
then a window cut from it with the few things that move drawn on top. Where
the window reaches past the world, the picture is the colour of outside.
"""

import math

import cv2
import numpy as np

from fleetless_turtle_sim.core.constants import (
    CELL_SIZE_M,
    PAD,
    ROCK,
    SQUARE_CELLS,
    TURTLE_RADIUS_M,
    WALL,
)
from fleetless_turtle_sim.core.world import in_grid, square_of

from . import constants as look
from .floor import cell_colours, lighter_and_darker
from .scene import Remembered, Scene

SCALE = round(CELL_SIZE_M * look.TOP_SIZE_PX / look.TOP_WINDOW_M)
"""Pixels along one cell."""

PX_PER_M = SCALE / CELL_SIZE_M

# OpenCV draws between pixels when it is given sixteenths of one.
SHIFT = 4
SIXTEENTHS = 1 << SHIFT

# A picture of nothing but outside, to begin a window with that reaches past
# the world. Kept, because filling one costs ten times what copying it does.
_NOTHING = np.empty((look.TOP_SIZE_PX, look.TOP_SIZE_PX, 3), np.uint8)
_NOTHING[:] = look.OUTSIDE


def render(scene: Scene) -> np.ndarray:
    """The picture as `[row, column]` of red, green and blue bytes, 480 x 480."""
    world = _world(scene.kinds)
    size = look.TOP_SIZE_PX
    north_edge_m = scene.kinds.shape[0] * CELL_SIZE_M

    # Where the turtle is in the world's picture, whose rows count from the
    # north. The window is cut on whole pixels around it, wherever it is:
    # a turtle far outside the world is the middle of a picture of nothing.
    left = round(scene.x * PX_PER_M) - size // 2
    top = round((north_edge_m - scene.y) * PX_PER_M) - size // 2
    picture = _window(world, top, left, size)

    def pixels(points) -> np.ndarray:
        """Metres to where OpenCV draws: sixteenths of a pixel, whole pixels at their centres."""
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        columns = points[:, 0] * PX_PER_M - left - 0.5
        rows = (north_edge_m - points[:, 1]) * PX_PER_M - top - 0.5
        return np.rint(np.stack([columns, rows], axis=1) * SIXTEENTHS).astype(np.int32)

    def length(metres: float) -> int:
        return round(metres * PX_PER_M * SIXTEENTHS)

    trail = pixels(scene.trail)
    if len(trail) >= 2:
        cv2.polylines(picture, [trail], False, look.TRAIL, look.TOP_TRAIL_PX, cv2.LINE_AA, SHIFT)

    # The plan is the path as it was planned, from where the turtle then
    # stood. What lies behind the turtle is driven, and the trail shows it.
    plan = pixels(_from_the_turtle_on(scene.plan, scene.x, scene.y))
    if len(plan) >= 2:
        cv2.polylines(picture, [plan], False, look.PLAN, look.TOP_PLAN_PX, cv2.LINE_AA, SHIFT)
    if len(plan) >= 1:
        target = plan[-1]
        ring, dot = length(look.TOP_TARGET_RADIUS_M), length(look.TOP_TARGET_DOT_M)
        cv2.circle(picture, target, ring, look.PLAN, look.TOP_PLAN_PX, cv2.LINE_AA, SHIFT)
        cv2.circle(picture, target, dot, look.PLAN, cv2.FILLED, cv2.LINE_AA, SHIFT)

    _draw_turtle(picture, scene, pixels, length)
    return picture


def _from_the_turtle_on(path, x: float, y: float) -> np.ndarray:
    """A path from the point of it that is nearest to (x, y) to its end.

    That point lies on one of the path's legs, or is a corner of it. The
    path's last point stays its last, so the target is where it was.
    """
    points = np.asarray(path, dtype=float).reshape(-1, 2)
    if len(points) < 2:
        return points
    here = np.array([x, y])
    starts, legs = points[:-1], points[1:] - points[:-1]
    squared = (legs**2).sum(axis=1)
    # How far along each leg its nearest point lies: 0 is the leg's start,
    # 1 its end. A leg without a length is its start.
    with np.errstate(divide="ignore", invalid="ignore"):
        along = np.where(squared > 0.0, ((here - starts) * legs).sum(axis=1) / squared, 0.0)
    nearest = starts + np.clip(along, 0.0, 1.0)[:, None] * legs
    leg = int(((nearest - here) ** 2).sum(axis=1).argmin())
    return np.vstack([nearest[leg], points[leg + 1 :]])


def _window(world: np.ndarray, top: int, left: int, size: int) -> np.ndarray:
    """A square cut from the world's picture. What it shows past the picture is outside."""
    rows, columns = world.shape[:2]
    first_row, past_row = max(top, 0), min(top + size, rows)
    first_column, past_column = max(left, 0), min(left + size, columns)
    if (past_row - first_row, past_column - first_column) == (size, size):
        # All of it lies in the world, as it does wherever the turtle is
        # more than half a window from the world's edge: one copy.
        return world[first_row:past_row, first_column:past_column].copy()
    picture = _NOTHING.copy()
    if first_row < past_row and first_column < past_column:
        picture[first_row - top : past_row - top, first_column - left : past_column - left] = world[
            first_row:past_row, first_column:past_column
        ]
    return picture


def _draw_turtle(picture: np.ndarray, scene: Scene, pixels, length) -> None:
    """A plate and an arrowhead that say where it is and where it looks, and the turtle on them.

    The plate covers what lies under the turtle, so nothing there is taken
    for a part of it. Plate and arrowhead are pale with a dark edge: one of
    the two shows on every floor.
    """
    ahead = np.array([math.cos(scene.heading), math.sin(scene.heading)])
    left = np.array([-ahead[1], ahead[0]])
    centre = np.array([scene.x, scene.y])

    def circle(at, radius_m: float, colour, thickness: int = cv2.FILLED) -> None:
        cv2.circle(picture, pixels(at)[0], length(radius_m), colour, thickness, cv2.LINE_AA, SHIFT)

    plate, edge = look.TOP_MARKER_RADIUS_M, look.TOP_MARKER_EDGE_PX
    circle(centre, plate, look.TURTLE_MARKER)
    circle(centre, plate, look.TURTLE_MARKER_EDGE, edge)
    tip = centre + look.TOP_ARROW_TIP * plate * ahead
    base = centre + look.TOP_ARROW_BASE * plate * ahead
    to_the_side = look.TOP_ARROW_HALF_WIDTH * plate * left
    arrowhead = pixels([tip, base + to_the_side, base - to_the_side])
    cv2.fillConvexPoly(picture, arrowhead, look.TURTLE_MARKER, cv2.LINE_AA, SHIFT)
    cv2.polylines(picture, [arrowhead], True, look.TURTLE_MARKER_EDGE, edge, cv2.LINE_AA, SHIFT)

    # The head first: the shell lies over where it joins.
    r = TURTLE_RADIUS_M
    head = centre + (r + look.TOP_HEAD_PAST_THE_RIM_M) * ahead
    circle(head, look.TOP_HEAD_RADIUS_M, look.TURTLE_SKIN)
    circle(centre, r, look.TURTLE_RIM)
    circle(centre, look.TOP_SHELL * r, look.TURTLE_SHELL)
    circle(centre, look.TOP_SHELL_TOP * r, look.TURTLE_SHELL_TOP)


# --- The world's picture, drawn once per world ------------------------------


def _enlarge(cells: np.ndarray) -> np.ndarray:
    """Every cell as SCALE x SCALE pixels."""
    return np.repeat(np.repeat(cells, SCALE, axis=0), SCALE, axis=1)


def _draw_world(kinds: np.ndarray) -> np.ndarray:
    """The whole world, north up."""
    north_up = np.flipud(kinds)
    picture = _enlarge(np.flipud(cell_colours(kinds)))
    _draw_lines(picture)
    _draw_pad_mark(picture, north_up)

    # How far a block's shadow reaches, per pixel: it stands for its height.
    reach = _enlarge(
        np.select(
            [north_up == WALL, north_up == ROCK],
            [look.TOP_WALL_SHADOW_PX, look.TOP_ROCK_SHADOW_PX],
            0,
        )
    )
    _cast_shadows(picture, reach)
    _draw_blocks(picture, north_up, reach)
    # Last, so a name is read whatever stands in its square's corner: the
    # garden's own wall runs through every corner of column A and row 16.
    _draw_names(picture)
    return picture


_world = Remembered(_draw_world)


def _thinly(picture: np.ndarray, drawn: np.ndarray) -> None:
    """Lays what was drawn on a copy over the picture, as thin as the lines and names are."""
    opacity = look.TOP_LINE_OPACITY
    cv2.addWeighted(drawn, opacity, picture, 1.0 - opacity, 0.0, dst=picture)


def _draw_lines(picture: np.ndarray) -> None:
    """The lines between the squares, on the floor: what stands on it stands on them too."""
    rows, columns = picture.shape[:2]
    step = SQUARE_CELLS * SCALE
    drawn = picture.copy()
    for column in range(step, columns, step):
        drawn[:, column] = look.SQUARE_LINES
    for row in range(rows - step, 0, -step):
        drawn[row] = look.SQUARE_LINES
    _thinly(picture, drawn)


def _draw_names(picture: np.ndarray) -> None:
    """Each square's name in its north-west corner."""
    rows, columns = picture.shape[:2]
    step = SQUARE_CELLS * SCALE
    drawn = picture.copy()
    for west in range(0, columns, step):
        for south in range(0, rows, step):
            # The squares are named A1 to P16. A world that is larger has
            # squares past them, and those go without a name.
            if not in_grid(west // SCALE, south // SCALE):
                continue
            name = square_of(west // SCALE, south // SCALE)
            # Text hangs from its lower left corner; a square's top is `step` above its south.
            east, down = look.TOP_NAME_INSET_PX
            corner = (west + east, rows - south - step + down)
            cv2.putText(
                drawn,
                name,
                corner,
                cv2.FONT_HERSHEY_SIMPLEX,
                look.TOP_NAME_SCALE,
                look.SQUARE_LINES,
                look.TOP_NAME_STROKE_PX,
                cv2.LINE_AA,
            )
    _thinly(picture, drawn)


def _draw_pad_mark(picture: np.ndarray, north_up: np.ndarray) -> None:
    """The bolt in the middle of the pad."""
    rows, columns = np.nonzero(north_up == PAD)
    if rows.size == 0:
        return
    top, left = rows.min() * SCALE, columns.min() * SCALE
    height = (rows.max() + 1) * SCALE - top
    width = (columns.max() + 1) * SCALE - left
    size = look.TOP_PAD_MARK_SIZE * min(width, height)
    corner = np.array([left + (width - size) / 2 - 0.5, top + (height - size) / 2 - 0.5])
    bolt = np.rint((corner + np.array(look.TOP_PAD_MARK) * size) * SIXTEENTHS).astype(np.int32)
    cv2.fillPoly(picture, [bolt], look.PAD_MARK, cv2.LINE_AA, SHIFT)


def _cast_shadows(picture: np.ndarray, reach: np.ndarray) -> None:
    """Darkens the floor to the north-east of every block, by as far as the block is tall."""
    shadow = np.zeros(reach.shape, bool)
    for distance in range(1, int(reach.max()) + 1):
        shadow[:-distance, distance:] |= reach[distance:, :-distance] >= distance
    shadow &= reach == 0
    picture[shadow] = (picture[shadow] * look.TOP_SHADOW).astype(np.uint8)


def _draw_blocks(picture: np.ndarray, north_up: np.ndarray, reach: np.ndarray) -> None:
    """Walls and rocks from above: their colour, a lit rim towards the sun, a dark one away."""
    lower = {name: np.zeros(reach.shape, bool) for name in ("west", "south", "east", "north")}
    lower["west"][:, 1:] = reach[:, :-1] < reach[:, 1:]
    lower["east"][:, :-1] = reach[:, 1:] < reach[:, :-1]
    lower["north"][1:] = reach[:-1] < reach[1:]
    lower["south"][:-1] = reach[1:] < reach[:-1]
    light = np.ones(reach.shape)
    light[lower["east"] | lower["north"]] = look.TOP_DARK_EDGE
    # The sun stands in the south-west. At a corner the lit rim wins.
    light[lower["west"] | lower["south"]] = look.TOP_LIT_EDGE

    kinds = _enlarge(north_up)
    # No two rocks quite alike; a wall is one thing.
    light[kinds == ROCK] *= _enlarge(np.flipud(lighter_and_darker(north_up.shape)))[kinds == ROCK]
    for kind, colour in ((WALL, look.WALL), (ROCK, look.ROCK)):
        where = kinds == kind
        picture[where] = np.clip(np.array(colour) * light[where][:, None], 0, 255)

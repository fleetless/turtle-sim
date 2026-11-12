# SPDX-License-Identifier: Apache-2.0
"""The `top` camera: the map around the turtle, north up.

A window of 12 m x 12 m, centred on the turtle, at four pixels a cell: the
cells by their kind with a hint of height, the lines between the squares and
their names, the pad, then the trail, the path, the target and the turtle.

The world is drawn once, whole, and kept until a cell changes. A picture is
then a window cut from it with the few things that move drawn on top.
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

MARGIN = look.TOP_SIZE_PX // 2 + 1
"""Around the world's picture, so a window never reaches past it."""

# OpenCV draws between pixels when it is given sixteenths of one.
SHIFT = 4
SIXTEENTHS = 1 << SHIFT

# A lightning bolt, the usual sign for "charge here": the view's, in a
# square from (0, 0) top left to (1, 1).
BOLT = ((0.58, 0.06), (0.24, 0.56), (0.47, 0.56), (0.40, 0.94), (0.76, 0.42), (0.52, 0.42))
BOLT_SIZE = 0.62
"""Of the pad's narrower side."""


def render(scene: Scene) -> np.ndarray:
    """The picture as `[row, column]` of red, green and blue bytes, 480 x 480."""
    world = _world(scene.kinds)
    size = look.TOP_SIZE_PX
    north_edge_m = scene.kinds.shape[0] * CELL_SIZE_M

    # Where the turtle is in the world's picture, whose rows count from the
    # north. The window is cut on whole pixels around it.
    turtle_column = MARGIN + scene.x * PX_PER_M
    turtle_row = MARGIN + (north_edge_m - scene.y) * PX_PER_M
    left = min(max(round(turtle_column) - size // 2, 0), world.shape[1] - size)
    top = min(max(round(turtle_row) - size // 2, 0), world.shape[0] - size)
    picture = world[top : top + size, left : left + size].copy()

    def pixels(points) -> np.ndarray:
        """Metres to where OpenCV draws: sixteenths of a pixel, whole pixels at their centres."""
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        columns = MARGIN + points[:, 0] * PX_PER_M - left - 0.5
        rows = MARGIN + (north_edge_m - points[:, 1]) * PX_PER_M - top - 0.5
        return np.rint(np.stack([columns, rows], axis=1) * SIXTEENTHS).astype(np.int32)

    def length(metres: float) -> int:
        return round(metres * PX_PER_M * SIXTEENTHS)

    trail = pixels(scene.trail)
    if len(trail) >= 2:
        cv2.polylines(picture, [trail], False, look.TRAIL, 1, cv2.LINE_AA, SHIFT)

    plan = pixels(scene.plan)
    if len(plan) >= 2:
        cv2.polylines(picture, [plan], False, look.PLAN, 2, cv2.LINE_AA, SHIFT)
    if len(plan) >= 1:
        target = plan[-1]
        cv2.circle(
            picture, target, length(look.TOP_TARGET_RADIUS_M), look.PLAN, 2, cv2.LINE_AA, SHIFT
        )
        cv2.circle(
            picture,
            target,
            length(0.3 * TURTLE_RADIUS_M),
            look.PLAN,
            cv2.FILLED,
            cv2.LINE_AA,
            SHIFT,
        )

    _draw_turtle(picture, scene, pixels, length)
    return picture


def _draw_turtle(picture: np.ndarray, scene: Scene, pixels, length) -> None:
    """A ring and an arrowhead that say where it is and where it looks, and the turtle in them."""
    ahead = np.array([math.cos(scene.heading), math.sin(scene.heading)])
    left = np.array([-ahead[1], ahead[0]])
    centre = np.array([scene.x, scene.y])

    def circle(at, radius_m: float, colour, thickness: int = cv2.FILLED) -> None:
        cv2.circle(picture, pixels(at)[0], length(radius_m), colour, thickness, cv2.LINE_AA, SHIFT)

    ring = look.TOP_MARKER_RADIUS_M
    circle(centre, ring, look.TURTLE_MARKER, 1)
    arrowhead = [
        centre + 1.75 * ring * ahead,
        centre + 1.12 * ring * ahead + 0.42 * ring * left,
        centre + 1.12 * ring * ahead - 0.42 * ring * left,
    ]
    cv2.fillConvexPoly(picture, pixels(arrowhead), look.TURTLE_MARKER, cv2.LINE_AA, SHIFT)

    # The head first: the shell lies over where it joins.
    r = TURTLE_RADIUS_M
    circle(centre + (r + 0.012) * ahead, 0.036, look.TURTLE_SKIN)
    circle(centre, r, look.TURTLE_RIM)
    circle(centre, 0.9 * r, look.TURTLE_SHELL)
    circle(centre, 0.5 * r, look.TURTLE_SHELL_TOP)


# --- The world's picture, drawn once per world ------------------------------


def _enlarge(cells: np.ndarray) -> np.ndarray:
    """Every cell as SCALE x SCALE pixels."""
    return np.repeat(np.repeat(cells, SCALE, axis=0), SCALE, axis=1)


def _draw_world(kinds: np.ndarray) -> np.ndarray:
    """The whole world, north up, with a margin of what lies outside around it."""
    north_up = np.flipud(kinds)
    picture = _enlarge(np.flipud(cell_colours(kinds)))
    _draw_squares(picture)
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

    rows, columns = picture.shape[:2]
    framed = np.empty((rows + 2 * MARGIN, columns + 2 * MARGIN, 3), np.uint8)
    framed[:] = look.OUTSIDE
    framed[MARGIN : MARGIN + rows, MARGIN : MARGIN + columns] = picture
    return framed


_world = Remembered(_draw_world)


def _draw_squares(picture: np.ndarray) -> None:
    """The lines between the squares and each square's name in its corner."""
    rows, columns = picture.shape[:2]
    step = SQUARE_CELLS * SCALE
    drawn = picture.copy()
    for column in range(step, columns, step):
        drawn[:, column] = look.SQUARE_LINES
    for row in range(rows - step, 0, -step):
        drawn[row] = look.SQUARE_LINES
    for west in range(0, columns, step):
        for south in range(0, rows, step):
            # The squares are named A1 to P16. A world that is larger has
            # squares past them, and those go without a name.
            if not in_grid(west // SCALE, south // SCALE):
                continue
            name = square_of(west // SCALE, south // SCALE)
            # Text hangs from its lower left corner; a square's top is `step` above its south.
            corner = (west + 4, rows - south - step + 13)
            cv2.putText(
                drawn,
                name,
                corner,
                cv2.FONT_HERSHEY_SIMPLEX,
                look.TOP_NAME_SCALE,
                look.SQUARE_LINES,
                1,
                cv2.LINE_AA,
            )
    opacity = look.TOP_LINE_OPACITY
    cv2.addWeighted(drawn, opacity, picture, 1.0 - opacity, 0.0, dst=picture)


def _draw_pad_mark(picture: np.ndarray, north_up: np.ndarray) -> None:
    """The bolt in the middle of the pad."""
    rows, columns = np.nonzero(north_up == PAD)
    if rows.size == 0:
        return
    top, left = rows.min() * SCALE, columns.min() * SCALE
    height = (rows.max() + 1) * SCALE - top
    width = (columns.max() + 1) * SCALE - left
    size = BOLT_SIZE * min(width, height)
    corner = np.array([left + (width - size) / 2 - 0.5, top + (height - size) / 2 - 0.5])
    bolt = np.rint((corner + np.array(BOLT) * size) * SIXTEENTHS).astype(np.int32)
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

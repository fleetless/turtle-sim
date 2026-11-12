# SPDX-License-Identifier: Apache-2.0
"""The floor: one colour per cell, as the view paints it.

Lawn in two greens that mark the squares, water with a lighter shore, the
charging pad with a darker rim. Both cameras start from it: `top` enlarges
it, `eye` looks along it.
"""

import numpy as np

from fleetless_turtle_sim.core.constants import PAD, ROCK, SQUARE_CELLS, WALL, WATER

from . import constants as look


def at_edge(inside: np.ndarray) -> np.ndarray:
    """Where a cell of `inside` has a neighbour, of its four, that is not. Past the grid is not."""
    padded = np.pad(inside, 1, constant_values=False)
    all_four = padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    return inside & ~all_four


def lighter_and_darker(shape: tuple[int, int]) -> np.ndarray:
    """A factor per cell: every other one a little lighter, the rest a little darker."""
    cy, cx = np.indices(shape)
    return np.where((cx + cy) % 2 == 0, 1.0 + look.CELL_CONTRAST, 1.0 - look.CELL_CONTRAST)


def cell_colours(kinds: np.ndarray) -> np.ndarray:
    """Every cell's floor colour as `[cy, cx]` of red, green and blue bytes."""
    cy, cx = np.indices(kinds.shape)
    other_square = (cx // SQUARE_CELLS + cy // SQUARE_CELLS) % 2 == 1
    colours = np.where(other_square[..., None], look.LAWN_OTHER_SQUARE, look.LAWN).astype(float)

    water = kinds == WATER
    colours[water] = look.WATER
    colours[at_edge(water)] = look.WATER_SHORE
    pad = kinds == PAD
    colours[pad] = look.PAD
    colours[at_edge(pad)] = look.PAD_EDGE
    colours[(kinds == WALL) | (kinds == ROCK)] = look.UNDER_BLOCKS

    colours *= lighter_and_darker(kinds.shape)[..., None]
    return np.clip(colours, 0, 255).astype(np.uint8)

# SPDX-License-Identifier: Apache-2.0
"""The floor both cameras start from: one colour per cell."""

import numpy as np

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_cameras.render.floor import at_edge, cell_colours
from fleetless_turtle_sim.core import constants as c


def shaded(colour, factor: float) -> tuple[int, ...]:
    return tuple(int(min(channel * factor, 255)) for channel in colour)


LIGHTER, DARKER = 1.0 + look.CELL_CONTRAST, 1.0 - look.CELL_CONTRAST


def test_there_is_a_colour_for_every_cell(kinds):
    colours = cell_colours(kinds)
    assert colours.shape == (c.GRID_CELLS, c.GRID_CELLS, 3)
    assert colours.dtype == np.uint8


def test_the_lawn_marks_the_squares_and_the_cells(kinds):
    colours = cell_colours(kinds)
    # Squares take turns, as on a chess board: B2 is A1's colour, B1 is the other.
    assert tuple(colours[20, 20]) == shaded(look.LAWN, LIGHTER)
    assert tuple(colours[20, 36]) == shaded(look.LAWN_OTHER_SQUARE, LIGHTER)
    assert tuple(colours[36, 36]) == shaded(look.LAWN, LIGHTER)
    # And so do the cells, more quietly.
    assert tuple(colours[20, 21]) == shaded(look.LAWN, DARKER)
    assert tuple(colours[21, 21]) == shaded(look.LAWN, LIGHTER)


def test_water_has_a_shore_and_the_pad_a_rim(kinds):
    kinds[100:110, 100:110] = c.WATER
    colours = cell_colours(kinds)
    assert tuple(colours[104, 104]) == shaded(look.WATER, LIGHTER)
    assert tuple(colours[100, 104]) == shaded(look.WATER_SHORE, LIGHTER)
    assert tuple(colours[104, 109]) == shaded(look.WATER_SHORE, DARKER)
    # The pad of the fixture is cells 3 to 14.
    assert tuple(colours[8, 8]) == shaded(look.PAD, LIGHTER)
    assert tuple(colours[3, 8]) == shaded(look.PAD_EDGE, DARKER)
    assert tuple(colours[14, 14]) == shaded(look.PAD_EDGE, LIGHTER)


def test_under_a_block_the_floor_is_one_colour(kinds):
    kinds[50, 50] = c.WALL
    kinds[50, 52] = c.ROCK
    colours = cell_colours(kinds)
    assert tuple(colours[50, 50]) == tuple(colours[50, 52]) == shaded(look.UNDER_BLOCKS, LIGHTER)


def test_the_edge_of_a_shape_is_where_a_neighbour_is_missing():
    inside = np.zeros((6, 6), bool)
    inside[1:5, 1:5] = True
    edge = at_edge(inside)
    assert edge.sum() == 12
    assert not edge[2:4, 2:4].any()
    # The grid's own border is an edge: past it is no cell of the shape.
    assert at_edge(np.ones((3, 3), bool)).sum() == 8

# SPDX-License-Identifier: Apache-2.0
"""The shipped map: what the spec asks of it, and that the committed files
are what tools/make_garden.py draws."""

import importlib.util
import json
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.world import World, square_of

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "maps" / "garden.png"
PLACES = ROOT / "maps" / "garden.places.json"
PICTURE = ROOT / "docs" / "garden.png"

_spec = importlib.util.spec_from_file_location("make_garden", ROOT / "tools" / "make_garden.py")
make_garden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_garden)


@pytest.fixture(scope="module")
def world():
    return World.load(MAP)


@pytest.fixture(scope="module")
def places():
    return {name: (cell["x"], cell["y"]) for name, cell in json.loads(PLACES.read_text()).items()}


def flood(passable: np.ndarray, seed: tuple[int, int], diagonal: bool = False) -> np.ndarray:
    """The cells of `passable[cy, cx]` connected to `seed`, as a mask."""
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if diagonal:
        steps += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    height, width = passable.shape
    reached = np.zeros_like(passable, dtype=bool)
    if not passable[seed[1], seed[0]]:
        return reached
    reached[seed[1], seed[0]] = True
    queue = deque([seed])
    while queue:
        cx, cy = queue.popleft()
        for dx, dy in steps:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < width and 0 <= ny < height and passable[ny, nx] and not reached[ny, nx]:
                reached[ny, nx] = True
                queue.append((nx, ny))
    return reached


# --- The committed files are the script's output ----------------------------


def test_the_committed_map_is_what_the_script_draws(world):
    assert np.array_equal(world.kinds, make_garden.draw())


def test_the_committed_places_are_what_the_script_writes():
    assert PLACES.read_text() == make_garden.places_text()


def test_the_committed_picture_is_what_the_script_draws():
    committed = cv2.cvtColor(cv2.imread(str(PICTURE), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)
    assert np.array_equal(committed, make_garden.picture(make_garden.draw()))


def test_the_script_draws_the_same_garden_twice():
    assert np.array_equal(make_garden.draw(), make_garden.draw())


# --- What the spec asks of the garden ---------------------------------------


def test_the_outer_wall_is_two_cells_thick(world):
    kinds = world.kinds
    for edge in (
        kinds[0],
        kinds[1],
        kinds[-1],
        kinds[-2],
        kinds[:, 0],
        kinds[:, 1],
        kinds[:, -1],
        kinds[:, -2],
    ):
        assert (edge == c.WALL).all()
    # ... and not three: the third ring is not wall all the way round.
    for third in (kinds[2, 2:-2], kinds[-3, 2:-2], kinds[2:-2, 2], kinds[2:-2, -3]):
        assert not (third == c.WALL).all()


def test_the_pad_is_twelve_by_twelve_in_a1_clear_of_the_wall(world):
    cys, cxs = np.nonzero(world.kinds == c.PAD)
    assert cxs.size == 144
    assert (cxs.max() - cxs.min() + 1, cys.max() - cys.min() + 1) == (12, 12)
    assert {square_of(cx, cy) for cx, cy in zip(cxs.tolist(), cys.tolist(), strict=True)} == {"A1"}
    # Clear of the wall: no pad cell has a blocking neighbour.
    around = world.kinds[cys.min() - 1 : cys.max() + 2, cxs.min() - 1 : cxs.max() + 2]
    assert not np.isin(around, c.BLOCKING).any()


def test_the_turtle_starts_on_the_pad(world):
    assert world.start_cell == (8, 8)
    assert world.is_pad(*world.start_cell)


def test_between_a_tenth_and_a_fifth_of_the_cells_block(world):
    share = np.isin(world.kinds, c.BLOCKING).mean()
    assert 0.10 <= share <= 0.20


def test_there_is_a_pond_of_at_least_300_cells(world):
    water = world.kinds == c.WATER
    cys, cxs = np.nonzero(water)
    largest = 0
    seen = np.zeros_like(water)
    for cx, cy in zip(cxs.tolist(), cys.tolist(), strict=True):
        if not seen[cy, cx]:
            pond = flood(water, (cx, cy))
            seen |= pond
            largest = max(largest, int(pond.sum()))
    assert largest >= 300


def test_there_are_rocks_to_weave_through(world):
    # The rock alley: six rocks in a lane between two hedges, left and right
    # in turn and past the middle, so no row of the lane is free end to end.
    lane = world.kinds[48:64, 48:161]
    assert (lane == c.ROCK).sum() == 6 * 54
    assert set(np.unique(lane)) == {c.FREE, c.ROCK}
    assert not any((row == c.FREE).all() for row in lane)
    assert (world.kinds[46:48, 48:161] == c.WALL).all()
    assert (world.kinds[64:66, 48:161] == c.WALL).all()


def test_the_places_are_what_their_names_say(world, places):
    assert set(places) == {"open", "far", "enclosed", "wall"}
    for name in ("open", "far", "enclosed"):
        assert world.kind(*places[name]) == c.FREE, name
        assert world.traversable(*places[name]), name
    assert world.kind(*places["wall"]) == c.WALL
    start_x, start_y = world.start_cell
    open_distance = (
        np.hypot(places["open"][0] - start_x, places["open"][1] - start_y) * c.CELL_SIZE_M
    )
    far_distance = np.hypot(places["far"][0] - start_x, places["far"][1] - start_y) * c.CELL_SIZE_M
    assert 2.0 <= open_distance <= 6.0  # a few metres from the start
    assert far_distance >= 25.0  # across the map


def test_the_enclosed_region_is_really_enclosed(world, places):
    free = ~np.isin(world.kinds, c.BLOCKING)
    region = flood(free, places["enclosed"], diagonal=True)
    assert region.sum() >= 100
    assert not region[world.start_cell[1], world.start_cell[0]]
    # Walls on all sides: every blocking cell next to the region is a wall.
    grown = np.zeros_like(region)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grown |= np.roll(np.roll(region, dy, axis=0), dx, axis=1)
    border = grown & ~region
    assert border.any()
    assert (world.kinds[border] == c.WALL).all()


def test_the_main_routes_are_at_least_six_cells_wide(world, places):
    """A six-cell-wide box can be pushed from the pad to `open` and to `far`."""
    blocked = np.isin(world.kinds, c.BLOCKING).astype(np.int32)
    total = np.pad(blocked, ((1, 0), (1, 0))).cumsum(axis=0).cumsum(axis=1)
    n = 6
    # box_free[y, x]: the n x n box whose lower-left cell is (x, y) holds no blocking cell.
    box_free = (total[n:, n:] - total[:-n, n:] - total[n:, :-n] + total[:-n, :-n]) == 0

    def boxes_holding(cell):
        cx, cy = cell
        return [
            (x, y)
            for x in range(max(cx - n + 1, 0), cx + 1)
            for y in range(max(cy - n + 1, 0), cy + 1)
            if y < box_free.shape[0] and x < box_free.shape[1] and box_free[y, x]
        ]

    start = boxes_holding(world.start_cell)
    assert start
    reached = flood(box_free, start[0])
    for name in ("open", "far"):
        assert any(reached[y, x] for x, y in boxes_holding(places[name])), name


def test_the_house_has_a_doorway_and_the_lane_a_dead_end(world, places):
    passable = np.zeros((c.GRID_CELLS, c.GRID_CELLS), dtype=bool)
    for cy in range(c.GRID_CELLS):
        for cx in range(c.GRID_CELLS):
            passable[cy, cx] = world.traversable(cx, cy)
    reached = flood(passable, world.start_cell, diagonal=False)
    # Both rooms of the house are reached, through the doorway and the door.
    assert reached[200, 50] and reached[200, 90]
    # The far end of the dead-end lane is reached; the cell behind its end wall
    # is reached only the long way round, and the wall between them stands.
    assert reached[128, 205]
    assert world.kind(209, 128) == c.WALL and world.kind(210, 128) == c.WALL
    assert reached[128, 214]
    # Nothing leads into the courtyard.
    assert not reached[places["enclosed"][1], places["enclosed"][0]]

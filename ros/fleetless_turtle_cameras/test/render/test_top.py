# SPDX-License-Identifier: Apache-2.0
"""The `top` camera: the map around the turtle, north up, against hand-made worlds."""

import math

import numpy as np
import pytest

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_cameras.render import top
from fleetless_turtle_cameras.render.scene import Scene
from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.world import cell_centre

SIZE = look.TOP_SIZE_PX
MIDDLE = SIZE // 2
PX_PER_M = SIZE / look.TOP_WINDOW_M
PX_PER_CELL = round(c.CELL_SIZE_M * PX_PER_M)


def scene_at(kinds, cx=100, cy=100, heading=0.0, **more) -> Scene:
    """The turtle on the centre of a cell, which is the middle of four pixels."""
    return Scene(kinds, *cell_centre(cx, cy), heading, **more)


def pixel_of(scene: Scene, x: float, y: float) -> tuple[int, int]:
    """The (row, column) of the pixel that holds a point, for a turtle on a cell's centre."""
    column = math.floor(MIDDLE + (x - scene.x) * PX_PER_M)
    row = math.floor(MIDDLE - (y - scene.y) * PX_PER_M)
    return row, column


def middle_of_cell(scene: Scene, cx: int, cy: int) -> tuple[int, int]:
    return pixel_of(scene, *cell_centre(cx, cy))


def colour_at(picture, where) -> tuple[int, ...]:
    return tuple(int(value) for value in picture[where])


def near(colour, expected, tolerance=0.04) -> bool:
    """The same colour but for the little that tells one cell from the next."""
    return all(abs(a - b) <= 255 * tolerance for a, b in zip(colour, expected, strict=True))


# --- The picture ------------------------------------------------------------


def test_the_picture_is_480_by_480_bytes_of_red_green_and_blue(kinds):
    picture = top.render(scene_at(kinds))
    assert picture.shape == (480, 480, 3)
    assert picture.dtype == np.uint8
    assert picture.flags.c_contiguous  # what a message's data is copied from


def test_a_cell_is_a_whole_number_of_pixels():
    # The world is drawn once by enlarging its cells; that needs whole pixels.
    assert pytest.approx(PX_PER_CELL) == c.CELL_SIZE_M * PX_PER_M
    assert PX_PER_CELL == top.SCALE == 4


def test_the_same_scene_is_the_same_picture(kinds):
    kinds[90:95, 104:110] = c.WALL
    scene = scene_at(kinds, heading=1.0, plan=np.array([[10.0, 10.0], [11.0, 10.5]]))
    assert np.array_equal(top.render(scene), top.render(scene))


# --- The turtle -------------------------------------------------------------


def test_the_turtle_is_drawn_at_the_centre(kinds):
    picture = top.render(scene_at(kinds))
    centre = picture[MIDDLE - 1 : MIDDLE + 1, MIDDLE - 1 : MIDDLE + 1]
    assert (centre == look.TURTLE_SHELL_TOP).all()
    # Its shell around that, a turtle's radius wide: nothing else is this dark.
    shell = (picture == look.TURTLE_SHELL).all(axis=2)
    rows, columns = np.nonzero(shell)
    assert rows.size > 0
    radius_px = c.TURTLE_RADIUS_M * PX_PER_M
    assert np.hypot(rows + 0.5 - MIDDLE, columns + 0.5 - MIDDLE).max() <= radius_px


def test_the_turtle_stays_at_the_centre_wherever_it_is(kinds):
    """The window is cut on whole pixels and the turtle drawn between them.

    So the turtle's own centre is never more than half a pixel from the
    picture's: one of the four pixels around the middle holds it and is the
    colour of the shell's top, and the other three are shell too.
    """
    positions = [(3.0, 3.0), (12.801, 7.777), (25.4, 25.4), (0.11, 25.49), (17.3333, 0.2)]
    # And every eighth of a pixel between two cells' centres, both ways.
    positions += [(10.05 + i * 0.003125, 10.05 - j * 0.003125) for i in range(9) for j in range(9)]
    for x, y in positions:
        picture = top.render(Scene(kinds, x, y, 0.0))
        centre = picture[MIDDLE - 1 : MIDDLE + 1, MIDDLE - 1 : MIDDLE + 1]
        assert (centre == look.TURTLE_SHELL_TOP).all(axis=2).any(), (x, y)
        shell = np.abs(centre.astype(int) - np.array(look.TURTLE_SHELL_TOP)).max(axis=2) < 40
        assert shell.all(), (x, y)


@pytest.mark.parametrize(
    ("heading", "row", "column"),
    [(0.0, 0, 1), (math.pi / 2, -1, 0), (math.pi, 0, -1), (-math.pi / 2, 1, 0)],
    ids=["east is right", "north is up", "west is left", "south is down"],
)
def test_the_arrowhead_points_where_the_turtle_looks(kinds, heading, row, column):
    picture = top.render(scene_at(kinds, heading=heading))
    # The arrowhead's middle lies 1.4 marker radii ahead of the turtle.
    ahead = round(1.4 * look.TOP_MARKER_RADIUS_M * PX_PER_M)
    marked = (picture == look.TURTLE_MARKER).all(axis=2)
    assert marked[MIDDLE + row * ahead, MIDDLE + column * ahead]
    assert not marked[MIDDLE - row * ahead, MIDDLE - column * ahead]


# --- The world --------------------------------------------------------------


def test_the_cells_are_drawn_by_their_kind(kinds):
    kinds[104:109, 110:115] = c.WALL
    kinds[90:95, 110:115] = c.ROCK
    kinds[104:109, 80:85] = c.WATER
    kinds[90:95, 80:85] = c.PAD
    scene = scene_at(kinds)
    picture = top.render(scene)
    # The middle cell of each block: its own colour, clear of rims and shores.
    assert colour_at(picture, middle_of_cell(scene, 112, 106)) == look.WALL
    assert near(colour_at(picture, middle_of_cell(scene, 112, 92)), look.ROCK)
    assert near(colour_at(picture, middle_of_cell(scene, 82, 106)), look.WATER)
    assert near(colour_at(picture, middle_of_cell(scene, 82, 92)), look.PAD)
    assert near(colour_at(picture, middle_of_cell(scene, 100, 104)), look.LAWN)
    # A shore and a rim: the outermost cells of water and pad are their own colours.
    assert near(colour_at(picture, middle_of_cell(scene, 80, 106)), look.WATER_SHORE)
    assert near(colour_at(picture, middle_of_cell(scene, 80, 92)), look.PAD_EDGE)


def test_north_is_up_and_east_is_right(kinds):
    kinds[120:124, 98:102] = c.WALL  # two metres north of the turtle
    kinds[98:102, 130:134] = c.WATER  # three metres east
    scene = scene_at(kinds)
    picture = top.render(scene)
    assert colour_at(picture, (MIDDLE - round(2.15 * PX_PER_M), MIDDLE)) == look.WALL
    assert near(colour_at(picture, (MIDDLE, MIDDLE + round(3.15 * PX_PER_M))), look.WATER)
    assert near(colour_at(picture, (MIDDLE + round(2.15 * PX_PER_M), MIDDLE)), look.LAWN)


def test_the_window_is_twelve_metres_wide(kinds):
    kinds[:, 158:160] = c.WALL  # from 5.75 m to 5.95 m east of the turtle: inside
    kinds[:, 161:163] = c.WATER  # from 6.05 m: outside
    picture = top.render(scene_at(kinds))
    last_of_the_wall = MIDDLE + round(5.95 * PX_PER_M) - 1
    assert last_of_the_wall == SIZE - 3
    assert (picture[:, last_of_the_wall - 2] == look.WALL).all()
    water = (np.abs(picture.astype(int) - np.array(look.WATER)) < 30).all(axis=2)
    assert not water.any()


def test_past_the_grid_the_picture_is_the_colour_of_outside(kinds):
    scene = scene_at(kinds, cx=0, cy=0)
    picture = top.render(scene)
    assert colour_at(picture, (MIDDLE + 20, MIDDLE - 20)) == look.OUTSIDE
    assert (picture[MIDDLE + 10 :, : MIDDLE - 10] == look.OUTSIDE).all()
    assert near(colour_at(picture, (MIDDLE - 100, MIDDLE + 100)), look.LAWN)


def test_a_line_runs_between_two_squares(kinds):
    # Squares are 16 cells: the border between B and C is at x = 3.2 m.
    scene = scene_at(kinds, cx=40, cy=40)
    picture = top.render(scene).astype(int)
    row, column = pixel_of(scene, 3.2, 4.5)
    on_the_line, beside_it = picture[row, column], picture[row, column + 2]
    assert on_the_line.sum() < beside_it.sum() - 60
    # And between rows 2 and 3, at y = 3.2 m.
    row, column = pixel_of(scene, 4.5, 3.2)
    assert picture[row, column].sum() < picture[row - 2, column].sum() - 60


def test_every_square_carries_its_name(kinds):
    """Not which letters: that a square's corner holds ink, and its middle none."""
    scene = scene_at(kinds, cx=40, cy=40)
    picture = top.render(scene).astype(int)
    # Square D3 begins at cell (48, 32). Its name stands in its north-west
    # corner; the turtle is in the square beside it.
    row, column = pixel_of(scene, 4.8, 4.8)
    corner = picture[row + 2 : row + 16, column + 2 : column + 24]
    middle = picture[row + 24 : row + 38, column + 24 : column + 46]
    darkest = lambda patch: patch.sum(axis=2).min()  # noqa: E731
    assert darkest(corner) < darkest(middle) - 60


def test_a_block_has_a_hint_of_height(kinds):
    kinds[104:110, 104:110] = c.WALL
    scene = scene_at(kinds, cx=90, cy=90)
    picture = top.render(scene).astype(int)
    brightness = lambda where: picture[where].sum()  # noqa: E731

    # Its shadow falls to the north-east: the floor there is darker than the
    # floor to the south-west, which the sun reaches.
    in_shadow = pixel_of(scene, 11.02, 10.8)  # just east of the wall
    in_the_sun = pixel_of(scene, 10.38, 10.6)  # just west of it
    assert brightness(in_shadow) < 0.9 * brightness(in_the_sun)
    north_of_it = pixel_of(scene, 10.8, 11.02)
    south_of_it = pixel_of(scene, 10.6, 10.38)
    assert brightness(north_of_it) < 0.9 * brightness(south_of_it)

    # And its rim is lit towards the sun and dark away from it.
    middle = brightness(middle_of_cell(scene, 107, 107))
    assert brightness(pixel_of(scene, 10.41, 10.7)) > middle  # the west rim
    assert brightness(pixel_of(scene, 10.99, 10.7)) < middle  # the east rim


def test_a_wall_casts_a_longer_shadow_than_a_rock(kinds):
    kinds[104:110, 104:110] = c.WALL
    kinds[104:110, 124:130] = c.ROCK
    scene = scene_at(kinds, cx=110, cy=100)
    picture = top.render(scene).astype(int)
    lawn = picture[middle_of_cell(scene, 116, 100)].sum()

    def shadow_px(east_edge_m: float) -> int:
        row, column = pixel_of(scene, east_edge_m + 0.001, 10.7)
        return sum(picture[row, column + i].sum() < 0.9 * lawn for i in range(8))

    assert shadow_px(11.0) == look.TOP_WALL_SHADOW_PX
    assert shadow_px(13.0) == look.TOP_ROCK_SHADOW_PX


def test_the_pad_carries_its_mark(kinds):
    scene = scene_at(kinds, cx=30, cy=30)  # beside the pad, which is cells 3 to 14
    picture = top.render(scene)
    mark = (picture == look.PAD_MARK).all(axis=2)
    assert mark.sum() > 50
    rows, columns = np.nonzero(mark)
    # The bolt stands in the middle of the pad, 0.9 m in both directions.
    row, column = pixel_of(scene, 0.9, 0.9)
    assert abs(rows.mean() - row) < 4 and abs(columns.mean() - column) < 4


# --- What moves -------------------------------------------------------------


def test_the_path_is_drawn_and_its_end_is_the_target(kinds):
    scene = scene_at(kinds)
    plan = np.array([[scene.x, scene.y], [scene.x + 2.0, scene.y], [scene.x + 2.0, scene.y + 3.0]])
    without = top.render(scene)
    picture = top.render(scene_at(kinds, plan=plan))
    drawn = (picture == look.PLAN).all(axis=2)
    assert not (without == look.PLAN).all(axis=2).any()
    # Along both legs of the path.
    assert drawn[pixel_of(scene, scene.x + 1.0, scene.y)]
    assert drawn[pixel_of(scene, scene.x + 2.0, scene.y + 1.5)]
    assert not drawn[pixel_of(scene, scene.x + 1.0, scene.y + 1.5)]
    # A ring around the last point, and a dot on it.
    target = pixel_of(scene, scene.x + 2.0, scene.y + 3.0)
    assert drawn[target]
    ring = round(look.TOP_TARGET_RADIUS_M * PX_PER_M)
    assert drawn[target[0], target[1] + ring] and drawn[target[0] - ring, target[1]]
    assert not drawn[target[0], target[1] + ring + 4]


def test_a_path_of_one_point_is_a_target(kinds):
    scene = scene_at(kinds, plan=np.array([[12.0, 12.0]]))
    drawn = (top.render(scene) == look.PLAN).all(axis=2)
    assert drawn[pixel_of(scene, 12.0, 12.0)]
    assert 20 < drawn.sum() < 200


def test_the_trail_is_drawn(kinds):
    scene = scene_at(kinds)
    trail = np.array([[scene.x - 3.0, scene.y - 1.0], [scene.x - 1.0, scene.y - 1.0]])
    picture = top.render(scene_at(kinds, trail=trail)).astype(int)
    without = top.render(scene).astype(int)
    changed = np.abs(picture - without).sum(axis=2) > 30
    assert changed[pixel_of(scene, scene.x - 2.0, scene.y - 1.0)]
    # A line one pixel wide, two metres long, and nothing else.
    rows, columns = np.nonzero(changed)
    assert rows.max() - rows.min() <= 2
    assert 70 <= columns.max() - columns.min() <= 82


def test_what_lies_outside_the_window_is_not_drawn_and_does_no_harm(kinds):
    far = np.array([[25.0, 25.0], [24.0, 24.0], [-30.0, 400.0]])
    scene = scene_at(kinds, cx=20, cy=20)
    assert np.array_equal(
        top.render(scene_at(kinds, cx=20, cy=20, plan=far, trail=far)), top.render(scene)
    )


def test_points_may_come_as_a_list(kinds):
    as_array = scene_at(kinds, plan=np.array([[10.5, 10.5], [11.0, 11.0]]))
    as_list = scene_at(kinds, plan=[(10.5, 10.5), (11.0, 11.0)])
    assert np.array_equal(top.render(as_array), top.render(as_list))


# --- The world is drawn once, and again when it changes ---------------------


def test_the_picture_changes_when_a_cell_does(kinds):
    scene = scene_at(kinds)
    before = top.render(scene)
    kinds[100, 110] = c.ROCK  # the same array, written to: a rock one metre east
    after = top.render(scene)
    where = middle_of_cell(scene, 110, 100)
    assert near(colour_at(before, where), look.LAWN)
    assert near(colour_at(after, where), look.ROCK)
    kinds[100, 110] = c.FREE
    assert np.array_equal(top.render(scene), before)


def test_a_world_of_another_size_is_drawn_too():
    small = np.full((40, 64), c.FREE, np.uint8)
    small[10:14, 40:44] = c.WALL
    scene = Scene(small, *cell_centre(32, 20), 0.0)
    picture = top.render(scene)
    assert picture.shape == (480, 480, 3)
    assert colour_at(picture, middle_of_cell(scene, 42, 12)) == look.WALL
    # The grid ends 2 m north of this turtle.
    assert colour_at(picture, (MIDDLE - round(2.2 * PX_PER_M), MIDDLE)) == look.OUTSIDE


def test_a_world_larger_than_the_simulators_is_drawn_and_its_far_squares_go_unnamed():
    large = np.full((300, 300), c.FREE, np.uint8)
    large[280:284, 280:284] = c.WALL
    scene = Scene(large, *cell_centre(278, 278), 0.0)
    picture = top.render(scene)
    assert colour_at(picture, middle_of_cell(scene, 282, 282)) == look.WALL
    # Square Q17 would begin at cell (256, 256). Its corner holds no ink.
    row, column = pixel_of(scene, 25.6, 27.2)
    corner = picture[row + 2 : row + 16, column + 2 : column + 24].astype(int)
    assert corner.sum(axis=2).min() > 0.9 * np.array(look.LAWN_OTHER_SQUARE).sum()

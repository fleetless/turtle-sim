# SPDX-License-Identifier: Apache-2.0
"""The `top` camera: the map around the turtle, north up, against hand-made worlds."""

import math
from pathlib import Path

import numpy as np
import pytest

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_cameras.render import top
from fleetless_turtle_cameras.render.scene import Scene
from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.world import World, cell_centre

GARDEN = Path(__file__).resolve().parents[4] / "maps" / "garden.png"

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
    # The arrowhead's middle lies halfway between its base and its tip.
    middle_of_it = (look.TOP_ARROW_BASE + look.TOP_ARROW_TIP) / 2
    ahead = round(middle_of_it * look.TOP_MARKER_RADIUS_M * PX_PER_M)
    marked = (picture == look.TURTLE_MARKER).all(axis=2)

    def around(at_row: int, at_column: int) -> np.ndarray:
        return marked[at_row - 1 : at_row + 2, at_column - 1 : at_column + 2]

    assert around(MIDDLE + row * ahead, MIDDLE + column * ahead).any()
    assert not around(MIDDLE - row * ahead, MIDDLE - column * ahead).any()


# --- The first picture: the turtle on the charging pad ------------------------


@pytest.fixture(scope="module")
def garden():
    return World.load(GARDEN)


def brightness(picture: np.ndarray) -> np.ndarray:
    return picture.astype(int).sum(axis=2)


def around_the_turtle(picture: np.ndarray):
    """For every pixel: how far its middle is from the picture's, and at which bearing."""
    rows, columns = np.indices(picture.shape[:2])
    east, north = columns + 0.5 - MIDDLE, MIDDLE - (rows + 0.5)
    return np.hypot(east, north), np.arctan2(north, east)


@pytest.mark.parametrize("heading_deg", range(0, 360, 45))
def test_on_the_pad_the_arrowhead_is_the_only_thing_beside_the_turtle(garden, heading_deg):
    """The first picture a user sees: the turtle where it starts, in the middle of the pad.

    The pad's bolt lies there too. It used to reach past the turtle's ring,
    and its two dark points stood beside the turtle like a second arrow.
    Now everything on the pad around the turtle's plate is pad, but for
    the arrowhead, which lies where the turtle looks.
    """
    x, y, _ = garden.start_pose
    heading = math.radians(heading_deg)
    picture = top.render(Scene(garden.kinds, x, y, heading))
    distance, bearing = around_the_turtle(picture)

    plate_px = look.TOP_MARKER_RADIUS_M * PX_PER_M
    # From clear of the plate's edge to 0.45 m: still on the pad, on every side.
    beside = (distance > plate_px + 2) & (distance < 0.45 * PX_PER_M)
    pad = (np.abs(picture.astype(int) - np.array(look.PAD)).max(axis=2) < 14) | (
        np.abs(picture.astype(int) - np.array(look.PAD_EDGE)).max(axis=2) < 14
    )
    not_pad = beside & ~pad
    assert not_pad.sum() > 15, "no arrowhead"
    off = np.abs((bearing[not_pad] - heading + math.pi) % (2 * math.pi) - math.pi)
    # As far to the side as the arrowhead's base reaches, and its edge's two
    # pixels more. The bolt's points stood 80 degrees and more off the heading.
    widest = math.atan2(look.TOP_ARROW_HALF_WIDTH, look.TOP_ARROW_BASE) + math.atan2(
        2, plate_px + 2
    )
    assert off.max() < widest < math.radians(45)


def test_the_pads_mark_lies_under_the_turtle_where_it_starts_and_shows_once_it_has_left(garden):
    x, y, heading = garden.start_pose
    at_the_start = top.render(Scene(garden.kinds, x, y, heading))
    assert not (at_the_start == look.PAD_MARK).all(axis=2).any()
    # The shipped pad is 1.2 m wide: its bolt is narrower than the plate.
    pad_side_m = 12 * c.CELL_SIZE_M
    assert look.TOP_PAD_MARK_SIZE * pad_side_m < 2 * look.TOP_MARKER_RADIUS_M

    gone = top.render(Scene(garden.kinds, x + 1.5, y, heading))
    mark = (gone == look.PAD_MARK).all(axis=2)
    assert mark.sum() > 8
    _, columns = np.nonzero(mark)
    assert columns.mean() < MIDDLE - 1.2 * PX_PER_M


@pytest.mark.parametrize(
    ("cell", "heading", "floor"),
    [((8, 8), 0.0, "the pad"), ((2, 100), math.pi, "the garden's wall")],
    ids=["on the pad", "against a wall"],
)
def test_the_arrowhead_has_a_dark_edge_where_its_own_colour_does_not_show(
    garden, cell, heading, floor
):
    """Pale on yellow and pale on a pale wall are hard to see. The edge is what is read."""
    picture = top.render(Scene(garden.kinds, *cell_centre(*cell), heading))
    distance, bearing = around_the_turtle(picture)
    plate_px = look.TOP_MARKER_RADIUS_M * PX_PER_M
    off = np.abs((bearing - heading + math.pi) % (2 * math.pi) - math.pi)
    where_it_is = (
        (distance > look.TOP_ARROW_BASE * plate_px - 1)
        & (distance < look.TOP_ARROW_TIP * plate_px + 1)
        & (off < math.radians(30))
    )
    inside, dark = brightness(picture)[where_it_is], np.array(look.TURTLE_MARKER_EDGE).sum()
    pale = np.array(look.TURTLE_MARKER).sum()
    # Its inside is its own pale colour, and around it runs a line far darker than the floor.
    assert (inside == pale).sum() >= 6, floor
    assert (inside < dark + 150).sum() >= 10, floor


def test_a_name_is_read_whatever_stands_in_its_squares_corner(kinds):
    """The same name on lawn and on a wall: the same letters, pixel for pixel."""
    scene = scene_at(kinds, cx=60, cy=40)
    # Square C3 begins at cell (32, 32); its north-west corner is at 3.2 m, 4.8 m.
    row, column = pixel_of(scene, 3.2, 4.8)
    name = (slice(row + 3, row + 16), slice(column + 2, column + 26))
    on_lawn = brightness(top.render(scene))[name]

    kinds[40:50, 29:42] = c.WALL  # over the corner and well around it
    on_wall = brightness(top.render(scene))[name]
    assert on_wall.max() == np.array(look.WALL).sum()

    # A letter's stroke is far darker than what it stands on, lawn or wall.
    letters_on_lawn = on_lawn < on_lawn.max() - 90
    letters_on_wall = on_wall < on_wall.max() - 130
    assert letters_on_lawn.sum() > 25
    assert (letters_on_lawn ^ letters_on_wall).sum() <= 0.15 * letters_on_lawn.sum()


def test_in_the_garden_the_names_along_the_outer_wall_are_drawn_on_it(garden):
    """Column A's names begin over the west wall, and row 16's reach up into the north one.

    The wall is eight pixels wide. A name begins four pixels into its
    square and its letters reach from five to thirteen pixels below the
    square's top, so part of every such name lies on the wall. It used to
    lie under it.
    """
    scene = Scene(garden.kinds, 3.0, 22.0, 0.0)
    picture = brightness(top.render(scene))
    wall = np.array(look.WALL).sum()
    east, down = look.TOP_NAME_INSET_PX

    def strokes(patch: np.ndarray) -> int:
        """Pixels of a letter on the wall: far darker than the wall is."""
        return int((patch < wall - 130).sum())

    # Squares A12 to A15 and B16 to D16 are in this picture.
    for number in (12, 13, 14, 15):
        row, column = pixel_of(scene, 0.0, number * 1.6)
        first_letter = picture[row + down - 9 : row + down + 1, column + east : column + 7]
        assert strokes(first_letter) >= 4, f"A{number}"
    for letter, index in (("B", 1), ("C", 2), ("D", 3)):
        row, column = pixel_of(scene, index * 1.6, 25.6)
        tops_of_the_letters = picture[row + down - 9 : row + 7, column + east : column + east + 20]
        assert strokes(tops_of_the_letters) >= 3, f"{letter}16"


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
    assert mark.sum() > 8
    rows, columns = np.nonzero(mark)
    # The bolt stands in the middle of the pad, 0.9 m in both directions,
    # and is a quarter as tall as the pad is wide.
    row, column = pixel_of(scene, 0.9, 0.9)
    assert abs(rows.mean() - row) < 3 and abs(columns.mean() - column) < 3
    side_px = look.TOP_PAD_MARK_SIZE * 1.2 * PX_PER_M
    assert 0.7 * side_px < rows.max() - rows.min() + 1 <= side_px


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


# --- Whatever the node lets through is drawn ---------------------------------


@pytest.mark.parametrize(
    ("rows", "columns"),
    [(1, 1), (1, 300), (300, 1), (look.WORLD_CELLS_LIMIT, look.WORLD_CELLS_LIMIT)],
    ids=["one cell", "one row", "one column", "the largest"],
)
def test_a_world_of_any_size_the_node_takes_is_drawn(rows, columns):
    cells = np.full((rows, columns), c.FREE, np.uint8)
    scene = Scene(cells, *cell_centre(0, 0), 0.0)
    picture = top.render(scene)
    assert picture.shape == (480, 480, 3)
    centre = picture[MIDDLE - 1 : MIDDLE + 1, MIDDLE - 1 : MIDDLE + 1]
    assert (centre == look.TURTLE_SHELL_TOP).all()
    # The world's south-west corner is under the turtle: south and west of it is nothing.
    assert colour_at(picture, (MIDDLE + 30, MIDDLE - 30)) == look.OUTSIDE
    # Lawn as far east and north as the world goes, and nothing past it:
    # looked at 30 pixels from the turtle, clear of its ring, and 30 from
    # the picture's edge.
    for cells_that_way, where in (
        (columns, lambda px: (MIDDLE, MIDDLE + px)),
        (rows, lambda px: (MIDDLE - px, MIDDLE)),
    ):
        to_the_edge_px = cells_that_way * PX_PER_CELL - PX_PER_CELL // 2
        for px in (30, MIDDLE - 30):
            colour = colour_at(picture, where(px))
            if px < to_the_edge_px:
                assert near(colour, look.LAWN, 0.08), (cells_that_way, px)
            else:
                assert colour == look.OUTSIDE, (cells_that_way, px)


def test_kinds_the_simulator_does_not_know_are_drawn_as_lawn(kinds):
    kinds[100, 104:108] = 9
    scene = scene_at(kinds)
    picture = top.render(scene)
    assert near(colour_at(picture, middle_of_cell(scene, 106, 100)), look.LAWN)


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (look.POSE_LIMIT_M, look.POSE_LIMIT_M),
        (-look.POSE_LIMIT_M, look.POSE_LIMIT_M),
        (look.POSE_LIMIT_M, -look.POSE_LIMIT_M),
        (-look.POSE_LIMIT_M, -look.POSE_LIMIT_M),
        (-7.0, 12.8),
        (12.8, 32.0),
    ],
)
def test_far_outside_the_world_the_turtle_is_the_middle_of_a_picture_of_nothing(kinds, x, y):
    picture = top.render(Scene(kinds, x, y, 1e300))
    centre = picture[MIDDLE - 1 : MIDDLE + 1, MIDDLE - 1 : MIDDLE + 1]
    assert (centre == look.TURTLE_SHELL_TOP).all(axis=2).any()
    # More than half a window from the world: no cell of it is in the picture.
    drawn = (picture != look.OUTSIDE).any(axis=2)
    rows, columns = np.nonzero(drawn)
    # What is drawn is the turtle, its plate and its arrowhead, with the
    # dark edge around the arrowhead's tip.
    reach_px = (look.TOP_ARROW_TIP + 0.2) * look.TOP_MARKER_RADIUS_M * PX_PER_M
    assert np.hypot(rows + 0.5 - MIDDLE, columns + 0.5 - MIDDLE).max() < reach_px


def test_a_turtle_just_outside_the_world_sees_the_edge_where_it_is(kinds):
    # Two metres west of the grid: the world begins two metres east of the middle.
    picture = top.render(Scene(kinds, -2.0, 12.85, 0.0))
    assert colour_at(picture, (MIDDLE + 40, MIDDLE + round(1.9 * PX_PER_M))) == look.OUTSIDE
    assert near(colour_at(picture, (MIDDLE + 40, MIDDLE + round(2.3 * PX_PER_M))), look.LAWN, 0.08)


def test_points_at_the_bound_do_no_harm(kinds):
    limit = look.POSE_LIMIT_M
    far = np.array(
        [[limit, limit], [-limit, limit], [10.5, 10.5], [limit, -limit], [-limit, -limit]]
    )
    picture = top.render(scene_at(kinds, plan=far, trail=far))
    assert picture.shape == (480, 480, 3)
    # The legs to and from the near point cross the window.
    assert (picture == look.PLAN).all(axis=2).any()


def test_the_bounds_leave_room_for_the_simulators_world():
    assert look.WORLD_CELLS_LIMIT >= c.GRID_CELLS
    assert look.POSE_LIMIT_M > look.WORLD_CELLS_LIMIT * c.CELL_SIZE_M

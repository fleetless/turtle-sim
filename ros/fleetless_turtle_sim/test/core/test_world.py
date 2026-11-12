# SPDX-License-Identifier: Apache-2.0
"""Map loading with its refusals, the coordinates, and where a circle fits."""

import cv2
import numpy as np
import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.world import (
    MapError,
    World,
    cell_centre,
    cell_of,
    in_grid,
    square_bounds,
    square_of,
)

WHITE, BLACK, GREY, BLUE, GREEN = (
    (255, 255, 255),
    (0, 0, 0),
    (128, 128, 128),
    (0, 0, 255),
    (0, 255, 0),
)


@pytest.fixture
def pixels():
    """A valid map as red-green-blue pixels, north up: white, with the pad in A1."""
    image = np.full((256, 256, 3), 255, np.uint8)
    # Cells 3 to 14 from the south are image rows 241 to 252 from the top.
    image[241:253, 3:15] = GREEN
    return image


def save(path, image):
    order = cv2.COLOR_RGBA2BGRA if image.ndim == 3 and image.shape[2] == 4 else cv2.COLOR_RGB2BGR
    assert cv2.imwrite(str(path), cv2.cvtColor(image, order) if image.ndim == 3 else image)
    return path


# --- Loading ----------------------------------------------------------------


def test_a_map_is_read_north_up_one_pixel_per_cell(tmp_path, pixels):
    pixels[0, 10] = BLACK  # top row: y = 255
    pixels[255, 20] = GREY  # bottom row: y = 0
    pixels[100, 30] = BLUE  # row 100 from the top: y = 155
    world = World.load(save(tmp_path / "map.png", pixels))
    assert world.kind(10, 255) == c.WALL
    assert world.kind(20, 0) == c.ROCK
    assert world.kind(30, 155) == c.WATER
    assert world.kind(3, 3) == c.PAD and world.kind(14, 14) == c.PAD
    assert world.kind(15, 15) == c.FREE
    assert world.kinds.shape == (256, 256) and world.kinds.dtype == np.uint8


def test_an_opaque_alpha_channel_changes_nothing(tmp_path, pixels):
    with_alpha = np.dstack([pixels, np.full((256, 256), 255, np.uint8)])
    world = World.load(save(tmp_path / "map.png", with_alpha))
    assert world.kind(3, 3) == c.PAD


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(MapError, match=r"^Map .*nowhere\.png does not exist\.$"):
        World.load(tmp_path / "nowhere.png")


def test_a_directory_is_refused_as_what_it_is(tmp_path):
    with pytest.raises(MapError, match=r"^Map .* is a directory: a map is one PNG file\.$"):
        World.load(tmp_path)


def test_a_file_that_is_no_image_is_refused(tmp_path):
    path = tmp_path / "map.png"
    path.write_text("not a picture")
    with pytest.raises(MapError, match=r"is not an image this simulator can read; use a PNG\.$"):
        World.load(path)


@pytest.mark.parametrize(("width", "height"), [(128, 128), (257, 256), (256, 255)])
def test_another_size_is_refused(tmp_path, width, height):
    image = np.full((height, width, 3), 255, np.uint8)
    with pytest.raises(
        MapError, match=rf"is {width} x {height} pixels; a map is exactly 256 x 256\.$"
    ):
        World.load(save(tmp_path / "map.png", image))


def test_another_colour_is_refused_naming_the_pixel_and_the_colour(tmp_path, pixels):
    pixels[40, 12] = (0x7F, 0x7F, 0x7F)  # what anti-aliasing leaves next to a rock
    pixels[90, 200] = (1, 2, 3)  # a later one: only the first is named
    with pytest.raises(MapError) as refusal:
        World.load(save(tmp_path / "map.png", pixels))
    sentence = str(refusal.value)
    assert "a pixel of colour #7f7f7f at column 12, row 40 from the top left" in sentence
    assert "#ffffff, #000000, #808080, #0000ff, #00ff00" in sentence
    assert sentence.endswith("(anti-aliasing is the usual cause).")


def test_a_translucent_pixel_is_another_colour(tmp_path, pixels):
    with_alpha = np.dstack([pixels, np.full((256, 256), 255, np.uint8)])
    with_alpha[5, 7, 3] = 0x80
    with pytest.raises(MapError, match=r"colour #ffffff80 at column 7, row 5 "):
        World.load(save(tmp_path / "map.png", with_alpha))


def test_sixteen_bit_channels_are_refused(tmp_path, pixels):
    deep = pixels.astype(np.uint16) * 257
    with pytest.raises(MapError, match=r"has 16-bit channels; a map is an 8-bit PNG\.$"):
        World.load(save(tmp_path / "map.png", deep))


def test_a_map_without_a_pad_is_refused(tmp_path):
    image = np.full((256, 256, 3), 255, np.uint8)
    with pytest.raises(MapError, match=r"has no charging pad \(#00ff00\)\.$"):
        World.load(save(tmp_path / "map.png", image))


def test_a_greyscale_file_is_read_and_then_lacks_its_pad(tmp_path):
    grey = np.full((256, 256), 255, np.uint8)
    grey[10, 10] = 0
    with pytest.raises(MapError, match=r"has no charging pad"):
        World.load(save(tmp_path / "map.png", grey))


def test_a_pad_the_turtle_does_not_fit_on_is_refused(tmp_path):
    image = np.full((256, 256, 3), 255, np.uint8)
    image[100:103, 100:103] = BLACK
    image[101, 101] = GREEN  # one pad cell, walled in
    with pytest.raises(
        MapError, match=r"has a charging pad, but no pad cell the turtle fits on\.$"
    ):
        World.load(save(tmp_path / "map.png", image))


def test_a_refusal_names_the_file(tmp_path):
    with pytest.raises(MapError, match="garden-draft.png"):
        World.load(tmp_path / "garden-draft.png")


def test_an_array_of_another_shape_is_refused():
    with pytest.raises(MapError, match=r"^The map is 10 x 20 cells; a map is exactly 256 x 256\.$"):
        World(np.zeros((20, 10), np.uint8))


# --- Coordinates ------------------------------------------------------------


def test_cells_and_metres():
    assert cell_of(0.0, 0.0) == (0, 0)
    assert cell_of(0.099, 0.05) == (0, 0)
    assert cell_of(0.101, 0.199) == (1, 1)
    assert cell_of(25.59, 25.59) == (255, 255)
    assert cell_of(-0.01, 25.61) == (-1, 256)
    assert cell_centre(0, 0) == pytest.approx((0.05, 0.05))
    assert cell_centre(255, 255) == pytest.approx((25.55, 25.55))
    assert cell_centre(12, 40) == pytest.approx((1.25, 4.05))


def test_every_cell_centre_lies_in_its_own_cell():
    for i in range(c.GRID_CELLS):
        assert cell_of(*cell_centre(i, i)) == (i, i)
        assert cell_of(*cell_centre(i, 255 - i)) == (i, 255 - i)


def test_in_grid():
    assert in_grid(0, 0) and in_grid(255, 255)
    assert not in_grid(-1, 0) and not in_grid(0, 256) and not in_grid(256, 0)


@pytest.mark.parametrize(
    ("cell", "square"),
    [
        ((0, 0), "A1"),
        ((15, 15), "A1"),
        ((16, 15), "B1"),  # across the border between two letters
        ((15, 16), "A2"),  # across the border between two numbers
        ((16, 16), "B2"),
        ((32, 96), "C7"),
        ((47, 111), "C7"),
        ((48, 112), "D8"),
        ((239, 239), "O15"),
        ((240, 240), "P16"),
        ((255, 255), "P16"),
        ((255, 0), "P1"),
        ((0, 255), "A16"),
    ],
)
def test_square_of_a_cell(cell, square):
    assert square_of(*cell) == square


def test_square_of_a_cell_outside_the_grid_is_an_error():
    with pytest.raises(ValueError, match=r"Cell \(256, 0\) is outside the grid\."):
        square_of(256, 0)


def test_square_bounds():
    assert square_bounds("A1") == (0, 0, 15, 15)
    assert square_bounds("C7") == (32, 96, 47, 111)
    assert square_bounds("P16") == (240, 240, 255, 255)
    assert square_bounds("A16") == (0, 240, 15, 255)


@pytest.mark.parametrize(
    "name", ["", "Z9", "Q1", "A0", "A17", "A01", "a1", "AA1", "P16 ", "7C", "A1\n"]
)
def test_what_names_no_square(name):
    assert square_bounds(name) is None


def test_squares_and_cells_agree_in_both_directions():
    for letter in "ABCDEFGHIJKLMNOP":
        for number in range(1, 17):
            name = f"{letter}{number}"
            x_min, y_min, x_max, y_max = square_bounds(name)
            assert (x_max - x_min, y_max - y_min) == (15, 15)
            for cell in [(x_min, y_min), (x_max, y_min), (x_min, y_max), (x_max, y_max)]:
                assert square_of(*cell) == name


# --- Where a circle fits ----------------------------------------------------


def test_a_circle_against_one_wall_cell(kinds):
    kinds[20, 20] = c.WALL  # covers 2.0 to 2.1 m both ways
    world = World(kinds)
    r = c.TURTLE_RADIUS_M
    assert not world.touches(1.9, 2.05, r)  # resting against its west face
    assert world.touches(1.905, 2.05, r)
    assert not world.touches(2.05, 2.2, r)  # resting against its north face
    assert world.touches(2.05, 2.195, r)
    # Past a corner the circle has room the square around it would not have.
    assert not world.touches(1.92, 1.92, r)
    assert world.touches(1.94, 1.94, r)
    assert not world.touches(1.0, 1.0, r)


@pytest.mark.parametrize("kind", [c.WALL, c.ROCK, c.WATER])
def test_wall_rock_and_water_block(kinds, kind):
    kinds[20, 20] = kind
    world = World(kinds)
    assert world.blocks(20, 20)
    assert world.touches(2.05, 2.05, c.TURTLE_RADIUS_M)
    assert not world.fits(2.05, 2.05)


def test_free_and_pad_do_not_block(world):
    assert not world.blocks(100, 100)
    assert not world.blocks(8, 8) and world.is_pad(8, 8)
    assert world.fits(0.85, 0.85)


def test_outside_the_grid_everything_blocks(world):
    r = c.TURTLE_RADIUS_M
    assert world.blocks(-1, 5) and world.blocks(5, 256)
    assert not world.is_pad(-1, 5)
    assert world.fits(0.1, 5.0)  # resting against the west edge
    assert not world.fits(0.095, 5.0)
    assert world.fits(25.5, 5.0)
    assert not world.fits(25.505, 5.0)
    assert not world.fits(5.0, -1.0)
    assert not world.touches(5.0, 0.1, r)
    assert world.touches(5.0, 0.099, r)


def test_traversable_keeps_the_planners_clearance(kinds):
    kinds[20, 20] = c.WALL
    world = World(kinds)
    assert not world.traversable(20, 20)  # the wall itself
    assert not world.traversable(19, 20)  # beside it
    assert not world.traversable(19, 19)  # at its corner
    assert world.traversable(18, 20)  # one cell of room: 0.15 m from centre to wall
    assert world.traversable(18, 18)
    assert not world.traversable(0, 100)  # the grid's edge counts
    assert world.traversable(1, 100)
    assert not world.traversable(-1, 100) and not world.traversable(100, 256)


def test_on_a_cell_centre_the_turtle_needs_its_eight_neighbours(kinds):
    kinds[21, 21] = c.ROCK
    world = World(kinds)
    assert not world.fits(*cell_centre(20, 20))  # the rock is the diagonal neighbour
    assert world.fits(*cell_centre(19, 19))


# --- The start --------------------------------------------------------------


def test_the_start_is_the_pad_cell_nearest_to_its_centroid_heading_east(world):
    # A 12 x 12 pad has four cells around its centroid; the lower-left one wins.
    assert world.start_cell == (8, 8)
    assert world.start_pose == pytest.approx((0.85, 0.85, 0.0))


def test_the_start_avoids_pad_cells_the_turtle_does_not_fit_on(kinds):
    kinds[7:11, 7:11] = c.ROCK  # a rock on the middle of the pad
    world = World(kinds)
    assert world.is_pad(*world.start_cell)
    assert world.traversable(*world.start_cell)
    assert world.start_cell == (8, 5)


def test_set_kind_and_restore(world):
    world.set_kind(50, 60, c.ROCK)
    assert world.kind(50, 60) == c.ROCK and world.blocks(50, 60)
    world.set_kind(50, 60, c.FREE)
    assert not world.blocks(50, 60)
    world.set_kind(51, 61, c.ROCK)
    world.restore()
    assert world.kind(51, 61) == c.FREE and not world.blocks(51, 61)

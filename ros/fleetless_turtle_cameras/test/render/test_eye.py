# SPDX-License-Identifier: Apache-2.0
"""The `eye` camera: the turtle's own view, against hand-made worlds and the core's laser."""

import math
import time
from pathlib import Path

import numpy as np
import pytest

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_cameras.render import eye, top
from fleetless_turtle_cameras.render.floor import cell_colours
from fleetless_turtle_cameras.render.scene import Scene
from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core import sensors
from fleetless_turtle_sim.core.world import World, cell_centre

GARDEN = Path(__file__).resolve().parents[4] / "maps" / "garden.png"

WIDTH, HEIGHT = look.EYE_WIDTH_PX, look.EYE_HEIGHT_PX
HORIZON = HEIGHT // 2
MIDDLE = WIDTH // 2
FOCAL_PX = (WIDTH / 2) / math.tan(look.EYE_FIELD_OF_VIEW / 2)

EAST, NORTH, WEST, SOUTH = 0.0, math.pi / 2, math.pi, -math.pi / 2

# The turtle of most tests stands on the centre of cell (50, 50), at
# 5.05 m, 5.05 m, and looks east. A block that begins at column 60 is then
# 0.95 m ahead.
AHEAD_M = 0.95


def scene_at(kinds, cx=50, cy=50, heading=EAST) -> Scene:
    return Scene(kinds, *cell_centre(cx, cy), heading)


def rows_of(distance_m: float, height_m: float) -> range:
    """The rows a face of that height covers at that distance ahead."""
    above = FOCAL_PX * (height_m - look.EYE_HEIGHT_M) / distance_m
    below = FOCAL_PX * look.EYE_HEIGHT_M / distance_m
    # A row belongs to the face if its middle does.
    return range(math.ceil(HORIZON - above - 0.5), math.floor(HORIZON + below - 0.5) + 1)


def near(colour, expected, tolerance=0.06) -> bool:
    """The same colour but for the light on a face, a cell's shade and a little haze."""
    return all(abs(int(a) - b) <= 255 * tolerance for a, b in zip(colour, expected, strict=True))


def is_like(picture, expected, tolerance=0.06) -> np.ndarray:
    """Per pixel: whether it is `expected` but for light, shade and haze."""
    return (np.abs(picture.astype(int) - np.array(expected)) <= 255 * tolerance).all(axis=-1)


def lit(colour, face) -> tuple[float, ...]:
    return tuple(channel * look.FACE_LIGHT[face] for channel in colour)


# --- The picture ------------------------------------------------------------


def test_the_picture_is_320_by_240_bytes_of_red_green_and_blue(kinds):
    picture = eye.render(scene_at(kinds))
    assert picture.shape == (240, 320, 3)
    assert picture.dtype == np.uint8
    assert picture.flags.c_contiguous


def test_it_is_70_degrees_wide():
    assert math.degrees(look.EYE_FIELD_OF_VIEW) == pytest.approx(70.0)
    # The outermost columns look 35 degrees to either side, within half a pixel's angle.
    outermost = math.degrees(math.atan((WIDTH / 2 - 0.5) / FOCAL_PX))
    assert outermost == pytest.approx(35.0, abs=0.2)


def test_on_open_floor_there_is_sky_above_and_floor_below(kinds):
    # In the middle of the world: the grid's edge is further away than the eye reaches.
    picture = eye.render(scene_at(kinds, cx=128, cy=128))
    assert is_like(picture[0], look.SKY).all()
    # The sky pales towards the horizon, and the far floor fades into the same haze.
    assert is_like(picture[HORIZON - 1], look.HAZE).all()
    assert is_like(picture[HORIZON], look.HAZE).all()
    assert is_like(picture[HEIGHT - 1], look.LAWN).all()
    assert not is_like(picture, look.WALL).any()


def test_the_same_scene_is_the_same_picture_and_the_cells_are_left_alone(kinds):
    kinds[40:60, 60:62] = c.WALL
    before = kinds.copy()
    scene = scene_at(kinds, heading=0.3)
    assert np.array_equal(eye.render(scene), eye.render(scene))
    assert np.array_equal(kinds, before)


# --- A wall -----------------------------------------------------------------


def test_the_picture_differs_between_a_wall_ahead_and_open_floor(kinds):
    open_floor = eye.render(scene_at(kinds))
    kinds[40:61, 60:62] = c.WALL
    wall_ahead = eye.render(scene_at(kinds))
    assert not np.array_equal(open_floor, wall_ahead)
    assert near(wall_ahead[HORIZON, MIDDLE], lit(look.WALL, eye.WEST))
    assert not near(open_floor[HORIZON, MIDDLE], lit(look.WALL, eye.WEST))


def test_a_wall_behind_the_turtle_changes_nothing(kinds):
    open_floor = eye.render(scene_at(kinds))
    kinds[:, 30:32] = c.WALL
    assert np.array_equal(eye.render(scene_at(kinds)), open_floor)


def test_a_wall_is_drawn_at_its_height_from_the_floor_up(kinds):
    kinds[:, 60:62] = c.WALL
    picture = eye.render(scene_at(kinds))
    rows = rows_of(AHEAD_M, look.WALL_HEIGHT_M)
    # 0.2 m of wall above the eye and 0.1 m below it, at 0.95 m.
    assert (rows.start, rows.stop - 1) == (72, 143)
    wall = is_like(picture, lit(look.WALL, eye.WEST))
    assert wall[rows.start : rows.stop].all()
    # Sky above it, floor below it, and its top is a straight line: the
    # wall crosses the view squarely, so no column sees it nearer.
    assert not wall[: rows.start].any() and not wall[rows.stop :].any()
    assert is_like(picture[rows.start - 1], look.HAZE, tolerance=0.2).all()
    assert is_like(picture[rows.stop], look.LAWN).all()


def test_a_wall_twice_as_far_is_half_as_tall(kinds):
    kinds[:, 60:62] = c.WALL
    near_by = is_like(eye.render(scene_at(kinds)), lit(look.WALL, eye.WEST)).sum(axis=0)
    kinds[:, 60:62] = c.FREE
    kinds[:, 69:71] = c.WALL  # 1.85 m ahead
    further = is_like(eye.render(scene_at(kinds)), lit(look.WALL, eye.WEST), 0.1).sum(axis=0)
    assert near_by[MIDDLE] == len(rows_of(AHEAD_M, look.WALL_HEIGHT_M)) == 72
    assert further[MIDDLE] == len(rows_of(1.85, look.WALL_HEIGHT_M)) == 37


def test_a_face_is_shaded_by_where_it_looks(kinds):
    # The same wall at the same distance, seen from its four sides.
    kinds[40:61, 60:62] = c.WALL  # east of the turtle: its face looks west
    kinds[60:62, 40:61] = c.WALL  # north: looks south
    kinds[40:61, 39:41] = c.WALL  # west: looks east
    kinds[39:41, 40:61] = c.WALL  # south: looks north
    brightness = {}
    for face, heading in (
        (eye.WEST, EAST),
        (eye.SOUTH, NORTH),
        (eye.EAST, WEST),
        (eye.NORTH, SOUTH),
    ):
        picture = eye.render(scene_at(kinds, heading=heading))
        row = picture[HORIZON, MIDDLE - 30 : MIDDLE + 30].astype(float)
        brightness[face] = row.mean()
        assert near(row.mean(axis=0), lit(look.WALL, face), tolerance=0.02), face
    # The sun stands in the south-west.
    assert (
        brightness[eye.SOUTH] > brightness[eye.WEST] > brightness[eye.EAST] > brightness[eye.NORTH]
    )


def test_a_face_fades_into_haze_with_its_distance(kinds):
    def colour_of_a_wall(column: int) -> np.ndarray:
        cells = kinds.copy()
        cells[:, column : column + 2] = c.WALL
        return eye.render(scene_at(cells))[HORIZON - 1, MIDDLE].astype(float)

    haze = np.array(look.HAZE, float)
    apart = [np.abs(colour_of_a_wall(column) - haze).sum() for column in (60, 80, 100, 120)]
    assert apart == sorted(apart, reverse=True)
    # At 7.05 m three quarters of the wall's colour are haze.
    assert apart[-1] < 0.35 * apart[0]


def test_beyond_the_range_nothing_is_drawn(kinds):
    open_floor = eye.render(scene_at(kinds))
    kinds[:, 132:134] = c.WALL  # 8.15 m ahead
    assert np.array_equal(eye.render(scene_at(kinds)), open_floor)
    kinds[:, 129:131] = c.WALL  # 7.85 m ahead
    assert not np.array_equal(eye.render(scene_at(kinds)), open_floor)


def test_left_in_the_world_is_left_in_the_picture(kinds):
    # In the middle of the world, a pillar ahead and to the left of a turtle
    # that looks east: north of its line of sight.
    kinds[134:137, 148:151] = c.WALL
    scene = scene_at(kinds, cx=128, cy=128)
    # Above the horizon there is only the pillar and the sky.
    wall = is_like(eye.render(scene)[:HORIZON], lit(look.WALL, eye.WEST))
    columns = np.nonzero(wall.any(axis=0))[0]
    assert columns.size > 0 and columns.max() < MIDDLE
    # Turned towards it, the turtle has it in the middle.
    towards = math.atan2(13.55 - scene.y, 14.95 - scene.x)
    turned = eye.render(scene_at(kinds, cx=128, cy=128, heading=towards))
    assert near(turned[HORIZON - 1, MIDDLE], lit(look.WALL, eye.WEST))


def test_the_grids_edge_is_a_face_as_tall_as_a_wall(kinds):
    picture = eye.render(scene_at(kinds, cx=246, cy=50))  # the edge is 0.95 m ahead
    rows = rows_of(AHEAD_M, look.WALL_HEIGHT_M)
    edge = is_like(picture, lit(look.OUTSIDE, eye.WEST))
    assert edge[rows.start : rows.stop].all()
    assert not edge[: rows.start].any() and not edge[rows.stop :].any()


# --- A rock is low, water is flat -------------------------------------------


def test_a_rock_is_lower_than_a_wall(kinds):
    kinds[:, 60:62] = c.ROCK
    picture = eye.render(scene_at(kinds))
    rows = rows_of(AHEAD_M, look.ROCK_HEIGHT_M)
    assert (rows.start, rows.stop - 1) == (108, 143)
    rock = is_like(picture, lit(look.ROCK, eye.WEST))
    assert rock[rows.start : rows.stop].all()
    assert not rock[: rows.start].any() and not rock[rows.stop :].any()
    assert look.EYE_HEIGHT_M < look.ROCK_HEIGHT_M < look.WALL_HEIGHT_M


def test_the_wall_behind_a_rock_rises_over_it(kinds):
    kinds[:, 60:62] = c.ROCK
    kinds[:, 70:72] = c.WALL  # 1.95 m ahead
    picture = eye.render(scene_at(kinds))
    rock_rows = rows_of(AHEAD_M, look.ROCK_HEIGHT_M)
    wall_rows = rows_of(1.95, look.WALL_HEIGHT_M)
    assert wall_rows.start < rock_rows.start
    rock = is_like(picture, lit(look.ROCK, eye.WEST))
    wall = is_like(picture, lit(look.WALL, eye.WEST), 0.1)
    assert rock[rock_rows.start : rock_rows.stop].all()
    assert wall[wall_rows.start : rock_rows.start].all()
    assert not wall[: wall_rows.start].any() and not wall[rock_rows.start :].any()


def test_a_rock_behind_a_wall_or_behind_a_rock_is_hidden(kinds):
    kinds[:, 60:62] = c.WALL
    wall_alone = eye.render(scene_at(kinds))
    kinds[:, 70:72] = c.ROCK
    assert np.array_equal(eye.render(scene_at(kinds)), wall_alone)

    kinds[:, 60:62] = c.ROCK
    kinds[:, 70:72] = c.FREE
    rock_alone = eye.render(scene_at(kinds))
    kinds[:, 70:72] = c.ROCK
    assert np.array_equal(eye.render(scene_at(kinds)), rock_alone)


def test_water_is_flat_and_the_far_bank_stands_behind_it(kinds):
    kinds[:, 60:80] = c.WATER  # from 0.95 m to 2.95 m ahead
    kinds[:, 80:82] = c.WALL
    picture = eye.render(scene_at(kinds))
    wall_rows = rows_of(2.95, look.WALL_HEIGHT_M)
    assert is_like(picture[wall_rows.start : wall_rows.stop], lit(look.WALL, eye.WEST), 0.1).all()
    # The water lies on the floor between its near shore and the wall's foot.
    near_shore = math.floor(HORIZON + FOCAL_PX * look.EYE_HEIGHT_M / AHEAD_M - 0.5)
    water = is_like(picture, look.WATER, 0.12)
    assert water[wall_rows.stop + 1 : near_shore - 1].all()
    assert not water[:HORIZON].any() and not water[near_shore + 2 :].any()
    assert is_like(picture[near_shore + 2 :], look.LAWN).all()


# --- The floor --------------------------------------------------------------


def test_the_floor_shows_the_cell_that_lies_there(kinds):
    kinds[40:61, 40:61] = c.FREE
    kinds[50, 52:54] = c.PAD
    scene = scene_at(kinds)
    picture = eye.render(scene)
    colours = cell_colours(kinds)
    for row in (150, 170, 200, 239):
        ahead_m = FOCAL_PX * look.EYE_HEIGHT_M / (row + 0.5 - HORIZON)
        cell = math.floor((scene.x + ahead_m) / c.CELL_SIZE_M)
        # Just left of the middle is just north of the line of sight: the turtle's own row of cells.
        assert near(picture[row, MIDDLE - 1], colours[50, cell], tolerance=0.02), row
    # The two pad cells are 0.15 m to 0.35 m ahead: a pad so small is all rim.
    rim = is_like(picture[:, MIDDLE - 1], look.PAD_EDGE)
    # The picture's lowest row shows the floor 0.19 m ahead.
    assert rim[HORIZON + math.ceil(FOCAL_PX * 0.1 / 0.33)]
    assert rim[HORIZON + math.floor(FOCAL_PX * 0.1 / 0.21) - 1]
    assert not rim[HORIZON + math.floor(FOCAL_PX * 0.1 / 0.37) - 1]


def test_the_floor_moves_when_the_turtle_does(kinds):
    scene = scene_at(kinds)
    one_cell_on = Scene(kinds, scene.x + c.CELL_SIZE_M, scene.y, scene.heading)
    assert not np.array_equal(eye.render(scene), eye.render(one_cell_on))


# --- Where no turtle should be ----------------------------------------------


@pytest.mark.parametrize(
    ("kind", "colour"), [(c.WALL, look.WALL), (c.ROCK, look.ROCK)], ids=["a wall", "a rock"]
)
def test_an_eye_inside_a_block_sees_that_block_and_nothing_else(kinds, kind, colour):
    kinds[50, 50] = kind
    picture = eye.render(scene_at(kinds))
    assert (picture == colour).all()


def test_an_eye_outside_the_grid_sees_the_colour_of_outside(kinds):
    picture = eye.render(Scene(kinds, -1.0, 5.0, EAST))
    assert (picture == look.OUTSIDE).all()


# --- Whatever the node lets through is drawn ---------------------------------


@pytest.mark.parametrize(
    ("rows", "columns"),
    [(1, 1), (1, 300), (300, 1), (look.WORLD_CELLS_LIMIT, look.WORLD_CELLS_LIMIT)],
    ids=["one cell", "one row", "one column", "the largest"],
)
def test_a_world_of_any_size_the_node_takes_is_drawn(rows, columns):
    cells = np.full((rows, columns), c.FREE, np.uint8)
    east = eye.render(Scene(cells, *cell_centre(0, 0), EAST))
    north = eye.render(Scene(cells, *cell_centre(0, 0), NORTH))
    assert east.shape == north.shape == (240, 320, 3)
    edge = lit(look.OUTSIDE, eye.WEST)
    # Looking along a side that is one cell long, the world's edge is
    # 0.05 m ahead and fills the picture. Along a longer one there is lawn
    # ahead, and the edge runs beside the turtle to the horizon.
    if columns == 1:
        assert is_like(east, edge).all()
    else:
        assert is_like(east[HEIGHT - 1, MIDDLE - 20 : MIDDLE + 20], look.LAWN, 0.1).all()
        assert not is_like(east[HORIZON - 30, MIDDLE - 2 : MIDDLE + 2], edge).any()
    if rows == 1:
        assert is_like(north, lit(look.OUTSIDE, eye.SOUTH)).all()
    else:
        assert is_like(north[HEIGHT - 1, MIDDLE - 20 : MIDDLE + 20], look.LAWN, 0.1).all()


def test_kinds_the_simulator_does_not_know_are_floor(kinds):
    open_floor = eye.render(scene_at(kinds, cx=128, cy=128))
    kinds[120:137, 138:140] = 9  # one metre ahead, where a wall would fill the view
    assert is_like(eye.render(scene_at(kinds, cx=128, cy=128))[HORIZON:], look.LAWN, 0.5).all()
    assert np.array_equal(
        eye.render(scene_at(kinds, cx=128, cy=128))[:HORIZON], open_floor[:HORIZON]
    )


@pytest.mark.parametrize(
    ("x", "y", "heading"),
    [
        (look.POSE_LIMIT_M, look.POSE_LIMIT_M, 0.0),
        (-look.POSE_LIMIT_M, look.POSE_LIMIT_M, 1e300),
        (look.POSE_LIMIT_M, -look.POSE_LIMIT_M, -1e300),
        (-look.POSE_LIMIT_M, -look.POSE_LIMIT_M, 4.0),
    ],
)
def test_at_the_bound_of_what_the_node_takes_the_eye_sees_outside(kinds, x, y, heading):
    assert (eye.render(Scene(kinds, x, y, heading)) == look.OUTSIDE).all()


# --- Against the core's laser ------------------------------------------------


@pytest.fixture(scope="module")
def garden():
    return World.load(GARDEN)


@pytest.mark.parametrize(
    ("x", "y", "heading"),
    [
        (0.85, 0.85, 0.0),  # the start, on the pad
        (5.23, 5.61, 0.1),  # in the rock alley
        (17.03, 4.47, 1.05),  # the orchard, towards the pond
        (4.81, 15.52, 1.57),  # in front of the house
        (9.07, 10.03, -0.4),  # by the rock beds
        (12.83, 12.79, 2.5),  # the middle of the lawn
        (24.91, 24.87, -2.2),  # the far corner
    ],
)
def test_every_ray_ends_where_the_cores_laser_says(garden, x, y, heading):
    """The eye and the laser cast rays on the same grid; they must agree.

    The laser stops at water too, so it is asked about a garden without
    any: there the nearest wall, rock or edge is what the eye finds first.
    """
    dry = garden.kinds.copy()
    dry[dry == c.WATER] = c.FREE
    world = World(dry)
    assert world.fits(x, y)

    sideways = (np.arange(WIDTH) + 0.5 - WIDTH / 2) / FOCAL_PX
    dx = math.cos(heading) + sideways * math.sin(heading)
    dy = math.sin(heading) - sideways * math.cos(heading)
    walls, rocks = eye.cast(dry, x, y, dx, dy)
    found = np.minimum(walls.distance, rocks.distance)

    for column in range(WIDTH):
        # A ray's distance counts the metres ahead; the laser's, the metres along the ray.
        slant = math.hypot(1.0, sideways[column])
        angle = heading - math.atan(sideways[column])
        along = sensors.cast(world, x, y, angle, look.EYE_RANGE_M * slant)
        if along is None:
            assert math.isinf(found[column]), column
        else:
            assert found[column] == pytest.approx(along / slant, abs=1e-9), column


def test_in_the_garden_the_two_pictures_show_the_same_world(garden):
    """From the start the turtle looks east along the garden's south wall, across the pad."""
    scene = Scene(garden.kinds, *garden.start_pose)
    picture = eye.render(scene)
    assert is_like(picture[HEIGHT - 1], look.PAD, 0.1).all()
    # The south wall, 0.65 m to the right, runs along the picture's right
    # edge and looks north.
    assert near(picture[HORIZON + 5, WIDTH - 1], lit(look.WALL, eye.NORTH))
    assert (top.render(scene)[240, 240] == look.TURTLE_SHELL_TOP).all()


# --- What it costs ----------------------------------------------------------


def test_ten_frames_a_second_of_both_stay_below_a_quarter_of_one_core(garden):
    """Measured in processor time, so a busy machine does not change the answer.

    A frame of both takes about 4 ms here, a tenth of what the bound allows:
    a quarter of a core at ten frames a second is 25 ms a frame.
    """
    frames = 40
    scenes = [
        Scene(garden.kinds, 12.8 + 0.05 * i, 12.8 - 0.03 * i, 0.07 * i) for i in range(frames)
    ]
    for scene in scenes[:3]:  # the world is drawn once; that is not a frame's cost
        top.render(scene), eye.render(scene)
    started = time.process_time()
    for scene in scenes:
        top.render(scene), eye.render(scene)
    per_frame = (time.process_time() - started) / frames
    print(f"top and eye together: {per_frame * 1000:.1f} ms of processor time a frame")
    assert per_frame < 0.25 / look.FRAME_RATE_HZ


@pytest.mark.parametrize(
    ("x", "y", "where"),
    [(12.8, 12.8, "in the middle of the world"), (0.85, 0.85, "in its corner, on the pad")],
)
def test_top_costs_a_window_cut_from_a_picture_and_little_more(garden, x, y, where):
    """Ten times less than the eye: the world is drawn once, a frame is a copy of a part.

    About 0.2 ms here, also where the window reaches past the world and
    begins as a picture of nothing. Filling that picture anew for every
    frame took 2 ms.
    """
    frames = 200
    scenes = [Scene(garden.kinds, x + 0.001 * i, y, 0.07 * i) for i in range(frames)]
    top.render(scenes[0])
    started = time.process_time()
    for scene in scenes:
        top.render(scene)
    per_frame = (time.process_time() - started) / frames
    print(f"top, {where}: {per_frame * 1000:.2f} ms of processor time a frame")
    assert per_frame < 0.0015

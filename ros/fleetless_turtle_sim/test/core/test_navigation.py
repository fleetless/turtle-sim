# SPDX-License-Identifier: Apache-2.0
"""Navigation: traversable cells, the way, the waypoints, and the following."""

import itertools
import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core import navigation as nav
from fleetless_turtle_sim.core.drive import Drive
from fleetless_turtle_sim.core.world import World, cell_centre, cell_of

ROOT = Path(__file__).resolve().parents[4]
GARDEN = ROOT / "maps" / "garden.png"
PLACES = ROOT / "maps" / "garden.places.json"

DT = c.STEP_S
CLEARANCE = c.TURTLE_RADIUS_M + c.PLANNER_CLEARANCE_M


def to(cell):
    """A navigator's target: this cell, as long as it is traversable."""
    return lambda passable: cell if passable[cell[1], cell[0]] else None


def follow(navigator, drive, seconds=600.0, each=None, crawling=False):
    """Let the navigator drive until it is over, or for `seconds`. Returns the seconds it took."""
    steps = 0
    while navigator.outcome is None and steps * DT < seconds:
        drive.command(*navigator.control(drive, DT))
        drive.step(DT, crawling=crawling)
        steps += 1
        if each is not None:
            each()
    return steps * DT


def turtle(world, cx, cy, heading=0.0):
    return Drive(world, *cell_centre(cx, cy), heading)


def never_closer_than(world, points, radius, spacing=0.005):
    """Whether a circle driven along the path, checked every few millimetres, touches nothing."""
    for (ax, ay), (bx, by) in itertools.pairwise(points):
        steps = max(1, math.ceil(math.dist((ax, ay), (bx, by)) / spacing))
        for i in range(steps + 1):
            x, y = ax + (bx - ax) * i / steps, ay + (by - ay) * i / steps
            # A hair less than the radius: resting against a cell is not touching it.
            if world.touches(x, y, radius - 1e-6):
                return False
    return True


@pytest.fixture(scope="module")
def garden():
    return World.load(GARDEN)


@pytest.fixture(scope="module")
def places():
    return {name: (cell["x"], cell["y"]) for name, cell in json.loads(PLACES.read_text()).items()}


# --- Traversable cells ------------------------------------------------------


def test_the_traversable_cells_are_the_worlds_own_answer_for_every_cell(kinds):
    kinds[40:44, 30:60] = c.WALL
    kinds[80, 80] = c.ROCK
    kinds[120:130, 100:110] = c.WATER
    world = World(kinds)
    passable = nav.traversable_cells(world)
    expected = np.array(
        [[world.traversable(cx, cy) for cx in range(c.GRID_CELLS)] for cy in range(c.GRID_CELLS)]
    )
    assert (passable == expected).all()


def test_a_traversable_cell_keeps_the_clearance_and_its_neighbour_towards_a_rock_does_not(kinds):
    kinds[80, 80] = c.ROCK
    passable = nav.traversable_cells(World(kinds))
    # The rock's west face is at 8.0 m. From the centre of cell 78 the grown
    # footprint reaches to 8.0 m and rests against it; from cell 79 it is in it.
    assert passable[80, 78] and not passable[80, 79]
    assert not passable[80, 80] and not passable[81, 81] and not passable[79, 79]
    assert passable[82, 80] and passable[78, 80]


def test_no_cell_at_the_grids_edge_is_traversable(world):
    passable = nav.traversable_cells(world)
    assert not passable[0, :].any() and not passable[-1, :].any()
    assert not passable[:, 0].any() and not passable[:, -1].any()
    assert passable[1:-1, 1:-1].all()  # the empty world: everything else is


# --- The way ----------------------------------------------------------------


def test_in_the_open_the_way_is_as_short_as_eight_neighbours_allow(world):
    passable = nav.traversable_cells(world)
    way = nav.find_path(passable, (8, 8), (40, 24))
    assert way[0] == (8, 8) and way[-1] == (40, 24)
    # Sixteen diagonal steps and sixteen straight ones, in whatever order.
    assert len(way) == 33
    steps = [(bx - ax, by - ay) for (ax, ay), (bx, by) in itertools.pairwise(way)]
    assert all(max(abs(dx), abs(dy)) == 1 for dx, dy in steps)
    assert sum(dx != 0 and dy != 0 for dx, dy in steps) == 16


def test_a_way_to_where_the_turtle_is_holds_that_cell(world):
    assert nav.find_path(nav.traversable_cells(world), (50, 50), (50, 50)) == [(50, 50)]


def test_every_cell_of_a_way_is_traversable_and_it_leads_round_a_wall(kinds):
    kinds[20:120, 60:62] = c.WALL  # a wall from y 20 to 119, across the straight line
    world = World(kinds)
    passable = nav.traversable_cells(world)
    way = nav.find_path(passable, (30, 60), (90, 60))
    assert all(passable[cy, cx] for cx, cy in way)
    # Round the south end, which is nearer than the north end.
    assert min(cy for _, cy in way) == 18
    assert max(cy for _, cy in way) == 60


def test_no_corner_is_cut():
    passable = np.zeros((c.GRID_CELLS, c.GRID_CELLS), bool)
    passable[10, 10] = passable[11, 11] = True
    # Two cells that touch at a corner, and nothing beside them: no way.
    assert nav.find_path(passable, (10, 10), (11, 11)) is None
    # With one of the two straight steps the way goes round the corner.
    passable[10, 11] = True
    assert nav.find_path(passable, (10, 10), (11, 11)) == [(10, 10), (11, 10), (11, 11)]
    # With both it may go straight across.
    passable[11, 10] = True
    assert nav.find_path(passable, (10, 10), (11, 11)) == [(10, 10), (11, 11)]


def test_a_way_never_squeezes_between_two_rocks_that_touch_at_a_corner(kinds):
    # A staircase of rocks from south-west to north-east, each touching the
    # next at a corner only. A turtle of two cells' width does not fit
    # through, and the planner does not try.
    for i in range(60):
        kinds[40 + i, 40 + i] = c.ROCK
    world = World(kinds)
    way = nav.find_path(nav.traversable_cells(world), (60, 50), (50, 60))
    crossed = [(cx, cy) for cx, cy in way if 40 <= cx < 100 and cx == cy]
    assert crossed == []
    assert len(way) > 40  # the long way round


def test_a_target_the_turtle_cannot_stand_on_has_no_way(kinds):
    kinds[100, 100] = c.ROCK
    passable = nav.traversable_cells(World(kinds))
    assert nav.find_path(passable, (8, 8), (100, 100)) is None  # the rock itself
    assert nav.find_path(passable, (8, 8), (101, 100)) is None  # beside it: no room
    assert nav.find_path(passable, (8, 8), (102, 100)) is not None


def test_an_enclosed_region_is_unreachable_and_that_is_known_at_once(kinds):
    kinds[100, 100:121] = c.WALL
    kinds[120, 100:121] = c.WALL
    kinds[100:121, 100] = c.WALL
    kinds[100:121, 120] = c.WALL
    world = World(kinds)
    passable = nav.traversable_cells(world)
    assert passable[110, 110]
    started = time.perf_counter()
    assert nav.find_path(passable, (8, 8), (110, 110)) is None
    assert nav.plan(world, passable, *cell_centre(8, 8), (110, 110)) is None
    assert time.perf_counter() - started < 0.5
    # From inside it, the rest of the world is as far away.
    assert nav.find_path(passable, (110, 110), (8, 8)) is None
    assert nav.find_path(passable, (110, 110), (105, 115)) is not None


# --- Line of sight ----------------------------------------------------------


def test_a_straight_line_is_clear_until_something_stands_in_it(kinds):
    world = World(kinds)
    a, b = cell_centre(20, 50), cell_centre(80, 50)
    assert nav.clear(world, *a, *b, CLEARANCE)
    kinds[50, 50] = c.ROCK
    assert not nav.clear(World(kinds), *a, *b, CLEARANCE)
    assert nav.clear(World(kinds), *cell_centre(20, 50), *cell_centre(40, 50), CLEARANCE)


def test_resting_against_a_cell_is_clear_and_a_hair_closer_is_not(kinds):
    kinds[52, 50] = c.ROCK  # its south face is at y = 5.2 m
    world = World(kinds)
    # A line at y = 5.05 m: the grown footprint reaches to 5.2 m exactly.
    assert nav.clear(world, 2.0, 5.05, 8.0, 5.05, CLEARANCE)
    assert not nav.clear(world, 2.0, 5.0501, 8.0, 5.0501, CLEARANCE)
    # The footprint alone has room to spare on the same line.
    assert nav.clear(world, 2.0, 5.0501, 8.0, 5.0501, c.TURTLE_RADIUS_M)


def test_a_line_past_a_corner_is_measured_from_the_corner(kinds):
    kinds[50, 50] = c.ROCK  # corners at (5.0, 5.0) and (5.1, 5.1)
    world = World(kinds)
    # A diagonal line that passes the rock's north-west corner (5.0, 5.1).
    near = 0.14 * math.sqrt(2)
    far = 0.16 * math.sqrt(2)
    assert not nav.clear(world, 4.0, 4.1 + near, 6.0, 6.1 + near, CLEARANCE)
    assert nav.clear(world, 4.0, 4.1 + far, 6.0, 6.1 + far, CLEARANCE)


def test_a_line_that_leaves_the_grid_is_not_clear(world):
    assert nav.clear(world, 0.15, 5.0, 3.0, 5.0, CLEARANCE)
    assert not nav.clear(world, 0.14, 5.0, 3.0, 5.0, CLEARANCE)
    assert not nav.clear(world, 20.0, 25.5, 22.0, 25.5, CLEARANCE)


def test_a_line_of_no_length_is_a_point(kinds):
    kinds[50, 50] = c.ROCK
    world = World(kinds)
    assert nav.clear(world, 4.5, 5.05, 4.5, 5.05, CLEARANCE)
    assert not nav.clear(world, 4.9, 5.05, 4.9, 5.05, CLEARANCE)


def test_clear_agrees_with_a_circle_pushed_along_the_line(garden):
    # Lines across the shipped garden, from a fan of points to a fan of points.
    starts = [cell_centre(cx, cy) for cx in (20, 110, 200) for cy in (30, 130, 235)]
    ends = [cell_centre(cx, cy) for cx in (45, 140, 240) for cy in (20, 100, 160, 240)]
    verdicts = set()
    for a in starts:
        for b in ends:
            is_clear = nav.clear(garden, *a, *b, CLEARANCE)
            verdicts.add(is_clear)
            assert is_clear == never_closer_than(garden, [a, b], CLEARANCE), (a, b)
    assert verdicts == {True, False}


# --- The plan ---------------------------------------------------------------


def test_in_the_open_the_plan_is_one_straight_line(world):
    passable = nav.traversable_cells(world)
    points = nav.plan(world, passable, 0.85, 0.85, (40, 24))
    assert points == ((0.85, 0.85), pytest.approx(cell_centre(40, 24)))
    assert nav.length(points) == pytest.approx(math.hypot(3.2, 1.6))


def test_a_plan_starts_where_the_turtle_stands_not_on_its_cells_centre(world):
    passable = nav.traversable_cells(world)
    points = nav.plan(world, passable, 5.02, 5.07, (90, 50))
    assert points == ((5.02, 5.07), pytest.approx(cell_centre(90, 50)))


def test_a_plan_round_a_wall_keeps_its_clearance_on_every_leg(kinds):
    kinds[20:120, 60:62] = c.WALL
    world = World(kinds)
    passable = nav.traversable_cells(world)
    points = nav.plan(world, passable, *cell_centre(30, 60), (90, 60))
    assert points[0] == pytest.approx(cell_centre(30, 60))
    assert points[-1] == pytest.approx(cell_centre(90, 60))
    assert 3 <= len(points) <= 5  # a few waypoints, not a cell at a time
    for a, b in itertools.pairwise(points):
        assert nav.clear(world, *a, *b, CLEARANCE)
    assert never_closer_than(world, points, CLEARANCE)
    # Shortened: not much longer than the tight way round the wall's end.
    tight = math.dist(points[0], (6.0, 1.9)) + math.dist((6.2, 1.9), points[-1]) + 0.2
    assert nav.length(points) < tight * 1.05


def test_every_plan_across_the_garden_keeps_its_clearance(garden, places):
    passable = nav.traversable_cells(garden)
    x, y, _ = garden.start_pose
    for target in [places["open"], places["far"], (100, 180), (60, 200), (200, 100), (100, 56)]:
        points = nav.plan(garden, passable, x, y, target)
        assert points is not None, target
        assert never_closer_than(garden, points, CLEARANCE), target
        waypoints = [cell_of(px, py) for px, py in points[1:]]
        assert all(passable[cy, cx] for cx, cy in waypoints), target


def test_a_plan_from_the_pad_to_far_takes_well_under_two_seconds(garden, places):
    x, y, _ = garden.start_pose
    started = time.perf_counter()
    passable = nav.traversable_cells(garden)
    points = nav.plan(garden, passable, x, y, places["far"])
    took = time.perf_counter() - started
    assert points is not None and points[-1] == pytest.approx(cell_centre(*places["far"]))
    assert took < 2.0, f"the plan took {took:.2f} s"


def test_the_gardens_enclosed_cell_and_its_wall_have_no_plan(garden, places):
    passable = nav.traversable_cells(garden)
    x, y, _ = garden.start_pose
    assert passable[places["enclosed"][1], places["enclosed"][0]]
    assert nav.plan(garden, passable, x, y, places["enclosed"]) is None
    assert nav.plan(garden, passable, x, y, places["wall"]) is None


# --- The nearest-cell start -------------------------------------------------


def test_a_turtle_close_to_a_rock_starts_at_the_nearest_traversable_cell(kinds):
    kinds[50, 52] = c.ROCK  # its north-west corner is at (5.2, 5.1)
    world = World(kinds)
    passable = nav.traversable_cells(world)
    # The turtle stands by that corner, in the cell diagonal to the rock:
    # it fits there, but the cell is not traversable.
    x, y = 5.12, 5.19
    assert world.fits(x, y) and cell_of(x, y) == (51, 51) and not passable[51, 51]
    assert nav.start_cell(world, passable, x, y) == (51, 52)
    points = nav.plan(world, passable, x, y, (20, 50))
    assert points[0] == (x, y)
    assert points[1] == pytest.approx(cell_centre(51, 52))
    assert nav.clear(world, *points[0], *points[1], c.TURTLE_RADIUS_M)
    for a, b in itertools.pairwise(points[1:]):
        assert nav.clear(world, *a, *b, CLEARANCE)


def test_a_turtle_on_a_traversable_cell_starts_on_its_own(world):
    assert nav.start_cell(world, nav.traversable_cells(world), 5.02, 5.07) == (50, 50)


def test_a_turtle_that_leans_on_a_wall_drives_clear_of_it_first(kinds):
    kinds[60:140, 52] = c.WALL  # a long wall, its west face at 5.2 m
    world = World(kinds)
    passable = nav.traversable_cells(world)
    x, y = 5.099, 8.05  # a millimetre from it, where a bump leaves a turtle
    points = nav.plan(world, passable, x, y, (50, 20))
    # Its own cell is traversable, but from where it stands a line with
    # room to spare leads nowhere. So: to the cell's centre first, on the
    # footprint's width, and the path proper starts there.
    assert nav.start_cell(world, passable, x, y) == (50, 80)
    assert points[1] == pytest.approx(cell_centre(50, 80))
    assert not nav.clear(world, *points[0], *points[1], CLEARANCE)
    assert nav.clear(world, *points[0], *points[1], c.TURTLE_RADIUS_M)
    for a, b in itertools.pairwise(points[1:]):
        assert nav.clear(world, *a, *b, CLEARANCE)


def test_the_nearest_cell_is_one_the_turtle_can_drive_to_not_one_behind_a_wall(kinds):
    # A lane two cells wide between a thick wall in the west and a thin one
    # in the east. No cell of the lane is traversable; the nearest ones that
    # are lie east of the thin wall, three cells away, behind it.
    kinds[40:100, 40:48] = c.WALL
    kinds[40:100, 50] = c.WALL
    world = World(kinds)
    passable = nav.traversable_cells(world)
    x, y = 4.9, 7.05  # in the middle of the lane
    assert world.fits(x, y)
    assert passable[70, 52] and not passable[70, 49] and not passable[70, 48]
    assert nav.start_cell(world, passable, x, y) is None
    assert nav.plan(world, passable, x, y, (120, 70)) is None


def test_without_a_traversable_cell_within_three_cells_there_is_no_start(kinds):
    kinds[40:100, 40:48] = c.WALL
    kinds[40:100, 50:58] = c.WALL
    world = World(kinds)
    passable = nav.traversable_cells(world)
    assert world.fits(4.9, 7.05)
    assert nav.start_cell(world, passable, 4.9, 7.05) is None
    # In the lane's mouth there is one, two cells south of the walls.
    assert world.fits(4.9, 3.95) and not passable[39, 49]
    assert nav.start_cell(world, passable, 4.9, 3.95) == (48, 38)


# --- Following --------------------------------------------------------------


def test_the_navigator_plans_drives_and_stands_on_the_target(world):
    drive = turtle(world, 8, 8)
    navigator = nav.Navigator(world, to((40, 24)))
    assert navigator.phase == "planning" and navigator.points == ()
    seconds = follow(navigator, drive)
    assert navigator.outcome == "reached" and navigator.target == (40, 24)
    assert math.dist((drive.x, drive.y), cell_centre(40, 24)) < c.GOAL_TOLERANCE_M
    assert (drive.linear, drive.angular) == (0.0, 0.0)
    # 3.58 m at half a metre a second, a turn before and the braking after.
    assert 7.5 < seconds < 9.0
    assert drive.collisions == 0
    assert drive.odometer == pytest.approx(math.hypot(3.2, 1.6), abs=0.01)


def test_it_turns_on_the_spot_until_it_faces_the_waypoint_within_five_degrees(world):
    drive = turtle(world, 50, 50, heading=0.0)
    navigator = nav.Navigator(world, to((50, 90)))  # straight north: a quarter turn
    start = (drive.x, drive.y)
    moved_at = None

    def watch():
        nonlocal moved_at
        if moved_at is None and (drive.x, drive.y) != start:
            moved_at = math.degrees(abs(math.pi / 2 - drive.heading))

    follow(navigator, drive, each=watch)
    assert navigator.outcome == "reached"
    assert moved_at is not None and moved_at <= 5.0
    assert moved_at > 0.5  # it does not wait for a perfect aim either


def test_it_stops_at_every_waypoint_and_turns_there(kinds):
    kinds[20:120, 60:62] = c.WALL
    world = World(kinds)
    drive = turtle(world, 30, 60)
    navigator = nav.Navigator(world, to((90, 60)))
    stood_at = []

    def watch():
        standing = drive.linear == 0.0 and drive.angular == 0.0
        if standing and (not stood_at or math.dist(stood_at[-1], (drive.x, drive.y)) > 0.05):
            stood_at.append((drive.x, drive.y))

    follow(navigator, drive, each=watch)
    assert navigator.outcome == "reached" and drive.collisions == 0
    # It stood on each waypoint after the first, to a few millimetres.
    assert len(stood_at) == len(navigator.points) - 1
    for stood, point in zip(stood_at, navigator.points[1:], strict=True):
        assert math.dist(stood, point) < 0.005


def test_it_stays_on_the_planned_path_to_the_millimetre(garden, places):
    drive = Drive(garden, *garden.start_pose)
    navigator = nav.Navigator(garden, to((100, 180)))
    worst = 0.0

    def watch():
        nonlocal worst
        here = np.array([drive.x, drive.y])
        nearest = math.inf
        for a, b in itertools.pairwise(navigator.points):
            a, b = np.array(a), np.array(b)
            along = np.clip(np.dot(here - a, b - a) / np.dot(b - a, b - a), 0.0, 1.0)
            nearest = min(nearest, float(np.linalg.norm(a + along * (b - a) - here)))
        worst = max(worst, nearest)

    follow(navigator, drive, each=watch)
    assert navigator.outcome == "reached" and drive.collisions == 0
    assert worst < 0.002


def test_it_keeps_the_limits_a_person_has(world):
    drive = turtle(world, 8, 8)
    navigator = nav.Navigator(world, to((200, 200)))
    fastest = turned = hardest = 0.0
    before = (0.0, 0.0)

    def watch():
        nonlocal fastest, turned, hardest, before
        fastest = max(fastest, abs(drive.linear))
        turned = max(turned, abs(drive.angular))
        hardest = max(hardest, abs(drive.linear - before[0]) / DT)
        before = (drive.linear, drive.angular)

    follow(navigator, drive, each=watch)
    assert navigator.outcome == "reached"
    assert fastest == pytest.approx(c.LINEAR_SPEED_LIMIT)
    assert turned <= c.ANGULAR_SPEED_LIMIT + 1e-9
    assert hardest <= c.LINEAR_ACCELERATION_LIMIT + 1e-6


def test_it_crawls_to_the_target_on_an_empty_battery(world):
    drive = turtle(world, 50, 50, heading=math.pi)  # facing away: half a turn first
    navigator = nav.Navigator(world, to((60, 50)))
    fastest = 0.0

    def watch():
        nonlocal fastest
        fastest = max(fastest, abs(drive.linear))

    seconds = follow(navigator, drive, each=watch, crawling=True)
    # The half turn alone takes longer than the watchdog's five seconds. A
    # turn is progress.
    assert navigator.outcome == "reached"
    assert fastest == pytest.approx(c.LINEAR_SPEED_LIMIT * c.CRAWL_LINEAR_FACTOR)
    assert math.pi / (c.ANGULAR_SPEED_LIMIT * c.CRAWL_ANGULAR_FACTOR) > c.STUCK_WINDOW_S
    assert seconds > 25.0  # six seconds of turning, twenty of crawling a metre


def test_a_target_under_the_turtle_is_reached_at_once(world):
    drive = turtle(world, 50, 50)
    navigator = nav.Navigator(world, to((50, 50)))
    assert follow(navigator, drive) <= 2 * DT
    assert navigator.outcome == "reached"


def test_a_moving_turtle_stands_before_the_plan_is_made(world):
    drive = turtle(world, 50, 50)
    for _ in range(50):
        drive.command(0.5, 1.0)
        drive.step(DT)
    navigator = nav.Navigator(world, to((150, 150)))
    while navigator.phase == "planning":
        assert navigator.points == ()
        drive.command(*navigator.control(drive, DT))
        drive.step(DT)
    # The plan starts from where it came to rest.
    assert navigator.points[0] == pytest.approx((drive.x, drive.y), abs=1e-3)
    assert navigator.version == 1


def test_no_way_and_no_target_are_outcomes_not_exceptions(kinds):
    kinds[100, 100:121] = kinds[120, 100:121] = c.WALL
    kinds[100:121, 100] = kinds[100:121, 120] = c.WALL
    world = World(kinds)
    drive = turtle(world, 8, 8)
    enclosed = nav.Navigator(world, to((110, 110)))
    follow(enclosed, drive)
    assert enclosed.outcome == "unreachable" and (drive.x, drive.y) == cell_centre(8, 8)
    on_the_wall = nav.Navigator(world, to((100, 110)))
    follow(on_the_wall, drive)
    assert on_the_wall.outcome == "target_occupied"


# --- The path check ---------------------------------------------------------


def test_a_block_on_the_path_stops_the_turtle_and_it_plans_again_from_there(world):
    drive = turtle(world, 20, 50)
    navigator = nav.Navigator(world, to((120, 50)))
    follow(navigator, drive, seconds=3.0)
    assert navigator.phase == "driving" and drive.linear == pytest.approx(0.5)
    before = navigator.points
    # A rock on the line, three metres ahead.
    world.set_kind(80, 50, c.ROCK)
    navigator.world_changed()
    drive.command(*navigator.control(drive, DT))
    assert navigator.phase == "replanning"
    phases = set()

    def watch():
        phases.add((navigator.phase, drive.linear == 0.0))

    while navigator.phase == "replanning":
        assert navigator.points == before  # the old path, until there is a new one
        drive.step(DT)
        drive.command(*navigator.control(drive, DT))
    # It stood when the new plan was made, and the plan starts there.
    assert drive.linear == 0.0
    assert navigator.version == 2 and len(navigator.points) >= 3
    assert navigator.points[0] == pytest.approx((drive.x, drive.y), abs=1e-6)
    assert never_closer_than(world, navigator.points, CLEARANCE)
    follow(navigator, drive, each=watch)
    assert navigator.outcome == "reached" and drive.collisions == 0


def test_without_being_told_the_path_is_checked_once_a_second(world):
    drive = turtle(world, 20, 50)
    navigator = nav.Navigator(world, to((120, 50)))
    follow(navigator, drive, seconds=3.0)
    world.set_kind(100, 50, c.ROCK)  # nobody calls world_changed()
    seconds = 0.0
    while navigator.phase == "driving":
        drive.command(*navigator.control(drive, DT))
        drive.step(DT)
        seconds += DT
    assert navigator.phase == "replanning"
    assert seconds <= c.PATH_CHECK_PERIOD_S + DT
    follow(navigator, drive)
    assert navigator.outcome == "reached" and drive.collisions == 0


def test_a_block_beside_the_path_or_behind_the_turtle_changes_nothing(world):
    drive = turtle(world, 20, 50)
    navigator = nav.Navigator(world, to((120, 50)))
    follow(navigator, drive, seconds=4.0)
    assert drive.x > 3.0
    world.set_kind(80, 53, c.ROCK)  # 0.25 m beside the line: the clearance holds
    world.set_kind(22, 50, c.ROCK)  # on the line, behind the turtle
    navigator.world_changed()
    follow(navigator, drive)
    assert navigator.outcome == "reached" and navigator.version == 1


def test_a_block_that_closes_the_only_way_makes_the_target_unreachable(kinds):
    # A wall across the whole world with one doorway.
    kinds[100:102, :] = c.WALL
    kinds[100:102, 60:68] = c.FREE
    world = World(kinds)
    drive = turtle(world, 64, 60)
    navigator = nav.Navigator(world, to((64, 140)))
    follow(navigator, drive, seconds=2.0)
    assert navigator.phase == "driving"
    for cx in range(60, 68):
        world.set_kind(cx, 100, c.ROCK)
    navigator.world_changed()
    follow(navigator, drive)
    assert navigator.outcome == "unreachable"
    assert (drive.linear, drive.angular) == (0.0, 0.0)
    assert drive.y < 10.0 - c.TURTLE_RADIUS_M and drive.collisions == 0


def test_a_block_on_the_target_makes_it_occupied(world):
    drive = turtle(world, 20, 50)
    navigator = nav.Navigator(world, to((120, 50)))
    follow(navigator, drive, seconds=2.0)
    world.set_kind(121, 50, c.ROCK)  # beside the target: the turtle no longer fits on it
    navigator.world_changed()
    follow(navigator, drive)
    assert navigator.outcome == "target_occupied"
    assert (drive.linear, drive.angular) == (0.0, 0.0)


# --- The stuck watchdog -----------------------------------------------------


def test_a_turtle_that_gets_nowhere_for_five_seconds_is_stuck(world):
    drive = turtle(world, 50, 50)
    navigator = nav.Navigator(world, to((50, 90)))
    follow(navigator, drive, seconds=3.0)
    assert navigator.phase == "driving" and drive.linear > 0.4
    # Something the map does not show holds the turtle: every move is refused.
    world.fits = lambda x, y: False
    held_at = (drive.x, drive.y)
    seconds = follow(navigator, drive)
    assert navigator.outcome == "stuck"
    assert (drive.x, drive.y) == held_at
    assert seconds == pytest.approx(c.STUCK_WINDOW_S, abs=2 * DT)


def test_a_turtle_held_from_the_start_is_stuck_five_seconds_after_its_turn(world):
    drive = turtle(world, 50, 50)
    world.fits = lambda x, y: False
    navigator = nav.Navigator(world, to((50, 90)))
    seconds = follow(navigator, drive)
    assert navigator.outcome == "stuck"
    # The quarter turn is progress; then nothing is.
    assert c.STUCK_WINDOW_S < seconds < c.STUCK_WINDOW_S + 1.5


def test_slow_progress_is_progress(world):
    drive = turtle(world, 50, 50)
    navigator = nav.Navigator(world, to((70, 50)))
    seconds = follow(navigator, drive, crawling=True)
    assert navigator.outcome == "reached"
    assert seconds > 2.0 / 0.05  # forty seconds for two metres, and no watchdog

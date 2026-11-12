# SPDX-License-Identifier: Apache-2.0
"""Navigation: a way from here to a cell, and how the turtle follows it.

Four parts, top to bottom:

- where the turtle may stand: the traversable cells;
- the way: A* over those cells;
- the waypoints: the way, shortened by line of sight;
- the `Navigator`, which follows them: turn on the spot, drive, stop, and
  again. It looks at the path once a second, plans again when something is
  in the way, and gives up when the turtle no longer gets anywhere.

The navigator only names velocity commands. They go through the same drive
as a person's, so the same limits hold, crawling included.
"""

import heapq
import itertools
import math
from collections.abc import Callable

import cv2
import numpy as np

from .constants import (
    ANGULAR_ACCELERATION_LIMIT,
    CELL_SIZE_M,
    FACING_TOLERANCE,
    GOAL_TOLERANCE_M,
    GRID_CELLS,
    HOLD_HEADING_WITHIN_M,
    LINEAR_ACCELERATION_LIMIT,
    PATH_CHECK_PERIOD_S,
    PLANNER_CLEARANCE_M,
    START_SEARCH_CELLS,
    STEERING_GAIN,
    STOP_WITHIN_M,
    STUCK_PROGRESS_M,
    STUCK_WINDOW_S,
    TIME_TOLERANCE_S,
    TOUCH_TOLERANCE_M2,
    TURTLE_RADIUS_M,
)
from .drive import Drive
from .world import World, cell_centre, cell_of, circle_reaches_cell

Cell = tuple[int, int]
Point = tuple[float, float]

PLANNING_RADIUS_M = TURTLE_RADIUS_M + PLANNER_CLEARANCE_M
"""What a planned path keeps clear of obstacles: the footprint and some room."""

# --- Where the turtle may stand ---------------------------------------------


def _reach(radius: float) -> list[Cell]:
    """The cells a circle on a cell's centre overlaps, as offsets from that cell."""
    span = math.ceil(radius / CELL_SIZE_M)
    x, y = cell_centre(0, 0)
    return [
        (dx, dy)
        for dy in range(-span, span + 1)
        for dx in range(-span, span + 1)
        if circle_reaches_cell(x, y, radius, dx, dy)
    ]


_REACH = _reach(PLANNING_RADIUS_M)
_SPAN = max(max(abs(dx), abs(dy)) for dx, dy in _REACH)


def traversable_cells(world: World) -> np.ndarray:
    """`[cy, cx]` is true where `world.traversable(cx, cy)` is: the whole grid at once.

    A cell is traversable when no cell the grown footprint overlaps blocks.
    Which cells those are does not depend on where the cell is, so the
    blocking cells are shifted once per such neighbour and laid over each
    other. Outside the grid everything blocks: the margin says so.
    """
    blocking = np.pad(world.blocking, _SPAN, constant_values=True)
    touched = np.zeros((GRID_CELLS, GRID_CELLS), bool)
    for dx, dy in _REACH:
        touched |= blocking[
            _SPAN + dy : _SPAN + dy + GRID_CELLS, _SPAN + dx : _SPAN + dx + GRID_CELLS
        ]
    return ~touched


def nearest_traversable(
    passable: np.ndarray, x: float, y: float, cells: tuple[int, int, int, int]
) -> Cell | None:
    """The traversable cell whose centre is nearest to (x, y), among `cells`.

    `cells` is (x_min, y_min, x_max, y_max), both corners included. A tie
    goes to the lower row, then the lower column, so the answer never
    depends on the order the cells are looked at.
    """
    x_min, y_min, x_max, y_max = cells
    cys, cxs = np.nonzero(passable[y_min : y_max + 1, x_min : x_max + 1])
    best = None
    for cx, cy in zip((cxs + x_min).tolist(), (cys + y_min).tolist(), strict=True):
        centre_x, centre_y = cell_centre(cx, cy)
        key = ((centre_x - x) ** 2 + (centre_y - y) ** 2, cy, cx)
        if best is None or key < best:
            best = key
    return None if best is None else (best[2], best[1])


# --- The way: A* over traversable cells -------------------------------------

_DIAGONAL_STEP = math.sqrt(2.0)
# Steps in a grid stored row by row: east, west, north, south.
_STRAIGHT = (1, -1, GRID_CELLS, -GRID_CELLS)
# A diagonal step, as the two straight steps it cuts short.
_DIAGONAL = ((1, GRID_CELLS), (1, -GRID_CELLS), (-1, GRID_CELLS), (-1, -GRID_CELLS))


def find_path(passable: np.ndarray, start: Cell, goal: Cell) -> list[Cell] | None:
    """The shortest way from `start` to `goal` over traversable cells, or None.

    Eight neighbours. A diagonal step is taken only where both straight
    steps beside it are traversable too, so no corner is cut.
    """
    if not (passable[start[1], start[0]] and passable[goal[1], goal[0]]):
        return None
    # A diagonal step needs both straight steps beside it, so it connects
    # nothing that straight steps do not. Whether there is a way at all is
    # therefore a question about regions, and answered in a millisecond.
    # A* would look at every cell it can reach before it gave up.
    _, regions = cv2.connectedComponents(passable.astype(np.uint8), connectivity=4)
    if regions[start[1], start[0]] != regions[goal[1], goal[0]]:
        return None

    # The cells as one list, row by row. No traversable cell lies on the
    # grid's edge, so a step from one never leaves the list.
    free = passable.ravel().tolist()
    first = start[1] * GRID_CELLS + start[0]
    last = goal[1] * GRID_CELLS + goal[0]
    goal_x, goal_y = goal

    def at_least(cell: int) -> float:
        """The length of the way from `cell` to the goal if nothing stood in it."""
        dx = abs(cell % GRID_CELLS - goal_x)
        dy = abs(cell // GRID_CELLS - goal_y)
        return max(dx, dy) + (_DIAGONAL_STEP - 1.0) * min(dx, dy)

    cost = {first: 0.0}
    came_from = {}
    settled = set()
    # (cost so far + at least what is left, what is left, cell). Among equals
    # the cell nearer to the goal comes first.
    frontier = [(at_least(first), at_least(first), first)]
    while frontier:
        _, _, cell = heapq.heappop(frontier)
        if cell == last:
            break
        if cell in settled:
            continue
        settled.add(cell)
        so_far = cost[cell]
        for step in _STRAIGHT:
            _reach_cell(cell + step, so_far + 1.0, cell, free, cost, came_from, frontier, at_least)
        for one, other in _DIAGONAL:
            if free[cell + one] and free[cell + other]:
                _reach_cell(
                    cell + one + other,
                    so_far + _DIAGONAL_STEP,
                    cell,
                    free,
                    cost,
                    came_from,
                    frontier,
                    at_least,
                )

    way = [last]
    while way[-1] != first:
        way.append(came_from[way[-1]])
    return [(cell % GRID_CELLS, cell // GRID_CELLS) for cell in reversed(way)]


def _reach_cell(cell, so_far, came, free, cost, came_from, frontier, at_least) -> None:
    """Note a way to `cell` if it is traversable and this way is shorter than any before."""
    if free[cell] and so_far < cost.get(cell, math.inf):
        cost[cell] = so_far
        came_from[cell] = came
        left = at_least(cell)
        heapq.heappush(frontier, (so_far + left, left, cell))


# --- Line of sight -----------------------------------------------------------


def clear(world: World, ax: float, ay: float, bx: float, by: float, radius: float) -> bool:
    """Whether a circle can move in a straight line from a to b.

    It can when it touches no blocking cell on the way and stays inside the
    grid. Exact for a circle wider than a cell, which the turtle is: such a
    circle cannot cross a cell without coming within reach of a corner.
    """
    size = GRID_CELLS * CELL_SIZE_M
    if min(ax, bx, ay, by) < radius - 1e-9 or max(ax, bx, ay, by) > size - radius + 1e-9:
        return False  # outside the grid everything blocks
    cx_min, cy_min = cell_of(min(ax, bx) - radius, min(ay, by) - radius)
    cx_max, cy_max = cell_of(max(ax, bx) + radius, max(ay, by) + radius)
    cx_min, cy_min = max(cx_min, 0), max(cy_min, 0)
    cys, cxs = np.nonzero(world.blocking[cy_min : cy_max + 1, cx_min : cx_max + 1])
    if cys.size == 0:
        return True
    left = (cxs + cx_min) * CELL_SIZE_M
    bottom = (cys + cy_min) * CELL_SIZE_M
    right, top = left + CELL_SIZE_M, bottom + CELL_SIZE_M
    reach = radius * radius - TOUCH_TOLERANCE_M2

    # The line's two ends against every cell, then every cell's four corners
    # against the line. One of these is where a line and a square are nearest.
    for x, y in ((ax, ay), (bx, by)):
        dx = x - np.clip(x, left, right)
        dy = y - np.clip(y, bottom, top)
        if (dx * dx + dy * dy < reach).any():
            return False
    ux, uy = bx - ax, by - ay
    length2 = ux * ux + uy * uy
    if length2 == 0.0:
        return True
    for corner_x in (left, right):
        for corner_y in (bottom, top):
            along = np.clip(((corner_x - ax) * ux + (corner_y - ay) * uy) / length2, 0.0, 1.0)
            dx = ax + along * ux - corner_x
            dy = ay + along * uy - corner_y
            if (dx * dx + dy * dy < reach).any():
                return False
    return True


# --- The plan: a way, shortened to waypoints --------------------------------


def start_cell(world: World, passable: np.ndarray, x: float, y: float) -> Cell | None:
    """The cell a plan starts at for a turtle that stands at (x, y).

    Its own cell if that is traversable. A turtle that stands close to an
    obstacle is on a cell that is not; then it is the nearest traversable
    cell within three cells that the turtle can drive to in a straight
    line, or None. Without the straight line, the nearest cell may well lie
    behind the wall the turtle leans on.
    """
    cx, cy = cell_of(x, y)
    if passable[cy, cx]:
        return cx, cy
    x_min, x_max = max(cx - START_SEARCH_CELLS, 0), min(cx + START_SEARCH_CELLS, GRID_CELLS - 1)
    y_min, y_max = max(cy - START_SEARCH_CELLS, 0), min(cy + START_SEARCH_CELLS, GRID_CELLS - 1)
    in_reach = np.zeros_like(passable)
    for ny in range(y_min, y_max + 1):
        for nx in range(x_min, x_max + 1):
            in_reach[ny, nx] = passable[ny, nx] and clear(
                world, x, y, *cell_centre(nx, ny), TURTLE_RADIUS_M
            )
    return nearest_traversable(in_reach, x, y, (x_min, y_min, x_max, y_max))


def plan(
    world: World, passable: np.ndarray, x: float, y: float, target: Cell
) -> tuple[Point, ...] | None:
    """The path from where the turtle stands to the centre of `target`, or None.

    The first point is (x, y), the last the target's centre, and between
    them lie the waypoints: the cells of the way at which the cell after
    could no longer be driven to in a straight line.
    """
    first = start_cell(world, passable, x, y)
    if first is None:
        return None
    way = find_path(passable, first, target)
    if way is None:
        return None

    points = [(x, y)]
    in_sight = None
    for cell in way:
        centre = cell_centre(*cell)
        if not clear(world, *points[-1], *centre, PLANNING_RADIUS_M):
            if in_sight is None:
                # The first cell of the way, and the room to it is short: a
                # turtle that leans on a wall. It drives there first, on the
                # footprint's width alone, and the path proper starts there.
                points.append(centre)
                continue
            # Not in a straight line from the last waypoint, so the cell
            # before is the next one. From there this cell is in sight: two
            # neighbours on the way always are.
            points.append(in_sight)
        in_sight = centre
    if in_sight is not None:
        points.append(in_sight)
    return tuple(points)


def length(points: tuple[Point, ...]) -> float:
    """The length of a path, in metres."""
    return sum(math.dist(a, b) for a, b in itertools.pairwise(points))


# --- Following ---------------------------------------------------------------


def _turn(angle: float) -> float:
    """An angle as the turn it asks for, the short way round: in (-pi, pi]."""
    return math.atan2(math.sin(angle), math.cos(angle))


class Navigator:
    """Takes the turtle to one cell: plans, follows, checks the path, gives up.

    `target_of` picks the target cell from the traversable cells, or None if
    there is none to pick. It is asked before every plan, because a block
    may since have landed on the cell it named last time.

    Every step, `control()` names the velocity command. Once `outcome` is
    set the drive is over: `reached`, or why not.
    """

    def __init__(self, world: World, target_of: Callable[[np.ndarray], Cell | None]):
        self.world = world
        self._target_of = target_of
        self.phase = "planning"
        """`planning`, `driving` or `replanning`."""
        self.outcome: str | None = None
        """`reached`, `target_occupied`, `unreachable` or `stuck`, once it is over."""
        self.target: Cell | None = None
        """The cell the current path leads to."""
        self.points: tuple[Point, ...] = ()
        """The current path: where the turtle stood, the waypoints, the target. Metres."""
        self.version = 0
        """Grows with every new path, so a publisher knows when to publish."""
        self._next = 1  # the point the turtle is on its way to
        self._beyond: tuple[float, ...] = ()  # metres from each point to the path's end
        self._driving = False  # on a leg: turning on the spot first, then driving
        self._tight_start = False  # the first leg was planned on the footprint's width alone
        self._check_due = False
        self._since_check = 0.0
        self._left_at_best = math.inf
        self._since_progress = 0.0

    def world_changed(self) -> None:
        """A cell changed: look at the path with the next step, not only at the next second."""
        self._check_due = True

    def remaining(self, x: float, y: float) -> float:
        """Metres from (x, y) along the current path to its end."""
        if self._next >= len(self.points):
            return 0.0
        return math.dist((x, y), self.points[self._next]) + self._beyond[self._next]

    def control(self, drive: Drive, dt: float) -> tuple[float, float]:
        """The velocity command for the step that follows: (linear, angular)."""
        standing = drive.linear == 0.0 and drive.angular == 0.0
        if self.phase != "driving":
            # A plan starts from where the turtle stands. So it stands first.
            if self.phase == "replanning":
                self._since_progress += dt
            if not standing or self._gave_up():
                return 0.0, 0.0
            self._plan(drive.x, drive.y)
            if self.outcome is not None:
                return 0.0, 0.0

        self._since_check += dt
        if self._check_due or self._since_check >= PATH_CHECK_PERIOD_S - TIME_TOLERANCE_S:
            self._check_due = False
            self._since_check = 0.0
            if self._blocked(drive.x, drive.y):
                self.phase = "replanning"
                return 0.0, 0.0
        return self._follow(drive, dt, standing)

    # --- Planning -------------------------------------------------------------

    def _plan(self, x: float, y: float) -> None:
        passable = traversable_cells(self.world)
        self.target = self._target_of(passable)
        if self.target is None:
            self.outcome = "target_occupied"
            return
        points = plan(self.world, passable, x, y, self.target)
        if points is None:
            self.outcome = "unreachable"
            return
        self.points = points
        self._beyond = tuple(length(points[index:]) for index in range(len(points)))
        self.version += 1
        self.phase = "driving"
        self._next = 1
        self._driving = False
        self._tight_start = not clear(self.world, *points[0], *points[1], PLANNING_RADIUS_M)
        self._check_due = False
        self._since_check = 0.0
        # What is left is measured against the new path from here on. The
        # clock of the watchdog runs on: planning is no progress.
        self._left_at_best = math.inf

    def _blocked(self, x: float, y: float) -> bool:
        """Whether something stands in what is left of the path."""
        for index in range(self._next, len(self.points)):
            ax, ay = self.points[index - 1]
            bx, by = self.points[index]
            radius = PLANNING_RADIUS_M
            if index == 1 and self._tight_start:
                radius = TURTLE_RADIUS_M
            if index == self._next:
                # What lies behind the turtle no longer matters. Measured
                # along the leg as planned, not from where the turtle
                # happens to be: a path that keeps its clearance to the
                # millimetre must not fail for a millimetre off course.
                ux, uy = bx - ax, by - ay
                length2 = ux * ux + uy * uy
                if length2 > 0.0:
                    along = min(max(((x - ax) * ux + (y - ay) * uy) / length2, 0.0), 1.0)
                    ax, ay = ax + along * ux, ay + along * uy
            if not clear(self.world, ax, ay, bx, by, radius):
                return True
        return False

    # --- Following ------------------------------------------------------------

    def _follow(self, drive: Drive, dt: float, standing: bool) -> tuple[float, float]:
        x, y = drive.x, drive.y
        to_x, to_y = self.points[self._next][0] - x, self.points[self._next][1] - y
        if standing and math.hypot(to_x, to_y) <= GOAL_TOLERANCE_M:
            # It stands on the waypoint. On to the next one, if there is one.
            self._next += 1
            self._driving = False
            self._left_at_best = math.inf
            if self._next == len(self.points):
                self.outcome = "reached"
                return 0.0, 0.0
            to_x, to_y = self.points[self._next][0] - x, self.points[self._next][1] - y
        distance = math.hypot(to_x, to_y)
        off = _turn(math.atan2(to_y, to_x) - drive.heading)
        ahead = to_x * math.cos(drive.heading) + to_y * math.sin(drive.heading)

        # The watchdog. What is left to do is the way along the path and the
        # turn still to make, the turn counted as the arc the turtle's rim
        # travels. As long as that shrinks, the turtle gets somewhere.
        left = self.remaining(x, y) + abs(off) * TURTLE_RADIUS_M
        if self._left_at_best == math.inf:
            self._left_at_best = left  # a new leg or a new path: measured from here
        elif left <= self._left_at_best - STUCK_PROGRESS_M:
            self._left_at_best = left
            self._since_progress = 0.0
        self._since_progress += dt
        if self._gave_up():
            return 0.0, 0.0

        if self._driving and standing and ahead <= STOP_WITHIN_M:
            # Stopped beside the waypoint or past it, and not on it. Aim again.
            self._driving = False
        if not self._driving:
            if drive.linear != 0.0:
                return 0.0, 0.0  # on the spot means on the spot
            if abs(off) > FACING_TOLERANCE:
                return 0.0, math.copysign(_speed_to_stop_within(abs(off), dt, angular=True), off)
            self._driving = True

        angular = 0.0
        if distance > HOLD_HEADING_WITHIN_M:
            if abs(off) > FACING_TOLERANCE:
                self._driving = False  # off the line: stop, and turn on the spot again
                return 0.0, 0.0
            angular = STEERING_GAIN * off
        linear = _speed_to_stop_within(ahead, dt) if ahead > STOP_WITHIN_M else 0.0
        return linear, angular

    def _gave_up(self) -> bool:
        if self._since_progress >= STUCK_WINDOW_S - TIME_TOLERANCE_S:
            self.outcome = "stuck"
        return self.outcome is not None


def _speed_to_stop_within(distance: float, dt: float, angular: bool = False) -> float:
    """The highest speed at which the turtle may take the next step and still stop in time.

    Metres and m/s, or radians and rad/s; the drive caps it at its limit.
    Braking from a speed v takes v²/2a. The step comes first, at full
    speed, before the brakes can bite: another v·dt/2. Solved for v, the
    turtle comes to rest on the point and not a few millimetres behind it.
    """
    limit = ANGULAR_ACCELERATION_LIMIT if angular else LINEAR_ACCELERATION_LIMIT
    lag = limit * dt / 2.0
    return math.sqrt(2.0 * limit * distance + lag * lag) - lag

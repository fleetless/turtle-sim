# SPDX-License-Identifier: Apache-2.0
"""The simulation: the one object the node holds.

It ties the world, the drive, the sensors and the battery together and
advances them in fixed steps. It reads no clock: `time` is the sum of the
steps taken, so a test runs an hour in a second and twice the same way.
"""

import math
from dataclasses import dataclass

import numpy as np

from . import sensors
from .battery import FULL, Battery
from .constants import (
    COMMAND_TIMEOUT_S,
    DISTANCE_TOLERANCE_M,
    FREE,
    GRID_CELLS,
    PAD,
    ROCK,
    STEP_S,
    TIME_TOLERANCE_S,
    TRAIL_MAX_POINTS,
    TRAIL_SPACING_M,
    TURTLE_RADIUS_M,
    WALL,
    WATER,
)
from .drive import Drive
from .world import World, cell_centre, cell_of, circle_reaches_cell, in_grid, square_of

_KIND_NAMES = {
    FREE: "free",
    WALL: "a wall",
    ROCK: "a rock",
    WATER: "water",
    PAD: "the charging pad",
}


def _heading_deg(heading: float) -> float:
    """A heading in degrees, in [0, 360), and still there as a float32.

    The status message carries a float32. A hair below a full turn, the
    nearest float32 is 360.0 itself, which the message says it never holds.
    A hair below a full turn is east.
    """
    degrees = math.degrees(heading) % 360.0
    return 0.0 if float(np.float32(degrees)) >= 360.0 else degrees


@dataclass(frozen=True)
class Status:
    """A snapshot with the fields of fleetless_turtle_msgs/msg/TurtleStatus."""

    cell_x: int
    cell_y: int
    square: str
    heading_deg: float
    speed: float
    turn_rate: float
    mode: str
    crawling: bool
    on_charger: bool
    bumper: bool
    collisions: int
    odometer_m: float
    trail: bool


class Simulation:
    def __init__(self, world: World, battery_drain: float = 1.0, step_s: float = STEP_S):
        self.world = world
        self.step_s = step_s
        self.battery = Battery(battery_drain)
        self.drive = Drive(world, *world.start_pose)
        self.time = 0.0
        """Simulated seconds since the start."""
        self.world_version = 0
        """Grows whenever a cell changes, so a publisher knows when to publish."""
        self.trail_on = False
        self.trail: list[tuple[float, float]] = []
        """The recorded trail, oldest point first, in metres."""
        self.trail_version = 0
        self._since_teleop = math.inf

    # --- Time ---------------------------------------------------------------

    def step(self) -> None:
        """Advance by exactly one fixed step."""
        dt = self.step_s
        self.drive.step(dt, crawling=self.battery.crawling)
        self.battery.step(dt, self.drive.linear, self.drive.angular, self.on_charger)
        self._since_teleop += dt
        self.time += dt
        if self.trail_on:
            self._record_trail()

    def command(self, linear: float, angular: float) -> None:
        """Take a velocity command: `linear.x` and `angular.z` of a Twist."""
        self.drive.command(linear, angular)
        # Only a non-zero command is a person at the controls. A value that is
        # not a number counted as zero.
        linear = linear if math.isfinite(linear) else 0.0
        angular = angular if math.isfinite(angular) else 0.0
        if linear != 0.0 or angular != 0.0:
            self._since_teleop = 0.0

    # --- What the services do -----------------------------------------------

    def reset(self) -> tuple[bool, str]:
        """Start pose, full battery, the map as loaded, trail off and empty, counters zero."""
        if self.world.restore():
            self.world_version += 1
        self.drive = Drive(self.world, *self.world.start_pose)
        self.battery.refill()
        self._since_teleop = math.inf
        self._clear_trail(on=False)
        return True, "The turtle is back on the pad, the battery is full, the map is as loaded."

    def teleport(self, x: int, y: int, heading_deg: float) -> tuple[bool, str]:
        """Put the turtle on the centre of a cell, with a heading."""
        if not in_grid(x, y):
            return (
                False,
                f"Cell ({x}, {y}) is outside the grid, 0 to {GRID_CELLS - 1}. Teleport declined.",
            )
        if not math.isfinite(heading_deg):
            return False, f"A heading of {heading_deg} is not an angle. Teleport declined."
        if self.world.blocks(x, y):
            return (
                False,
                f"Cell ({x}, {y}) is {_KIND_NAMES[self.world.kind(x, y)]}. Teleport declined.",
            )
        centre_x, centre_y = cell_centre(x, y)
        if not self.world.fits(centre_x, centre_y):
            return False, f"The turtle does not fit on cell ({x}, {y}). Teleport declined."
        heading_deg = heading_deg % 360.0
        self.drive.place(centre_x, centre_y, math.radians(heading_deg))
        return True, f"The turtle is on cell ({x}, {y}), heading {heading_deg:g} degrees."

    def set_block(self, x: int, y: int, occupied: bool) -> tuple[bool, str]:
        """Put a rock on a cell, or free the cell, whatever it was."""
        if not in_grid(x, y):
            return (
                False,
                f"Cell ({x}, {y}) is outside the grid, 0 to {GRID_CELLS - 1}. Nothing changed.",
            )
        if self.world.is_pad(x, y):
            return False, f"Cell ({x}, {y}) is part of the charging pad. Nothing changed."
        if circle_reaches_cell(self.drive.x, self.drive.y, TURTLE_RADIUS_M, x, y):
            return False, f"The turtle's footprint touches cell ({x}, {y}). Nothing changed."
        kind = ROCK if occupied else FREE
        if self.world.kind(x, y) != kind:
            self.world.set_kind(x, y, kind)
            self.world_version += 1
        return True, f"Cell ({x}, {y}) is {_KIND_NAMES[kind]} now."

    def set_trail(self, on: bool) -> tuple[bool, str]:
        """`True` records the trail, `False` stops and clears it."""
        if not on:
            self._clear_trail(on=False)
            return True, "The trail is off and empty."
        if not self.trail_on:
            self._clear_trail(on=True)
            self._record_trail()
        return True, "The trail is being recorded."

    # --- State --------------------------------------------------------------

    @property
    def cell(self) -> tuple[int, int]:
        """The cell that holds the turtle's centre."""
        return cell_of(self.drive.x, self.drive.y)

    @property
    def on_charger(self) -> bool:
        return self.world.is_pad(*self.cell)

    @property
    def mode(self) -> str:
        """By precedence: teleop, charging, idle.

        `docking` and `auto` rank above these; they belong to goals, and this
        turtle has none yet.
        """
        if self._since_teleop < COMMAND_TIMEOUT_S - TIME_TOLERANCE_S:
            return "teleop"
        if self.on_charger and self.battery.percent < FULL:
            return "charging"
        return "idle"

    def status(self) -> Status:
        cell_x, cell_y = self.cell
        return Status(
            cell_x=cell_x,
            cell_y=cell_y,
            square=square_of(cell_x, cell_y),
            heading_deg=_heading_deg(self.drive.heading),
            speed=self.drive.linear,
            turn_rate=self.drive.angular,
            mode=self.mode,
            crawling=self.battery.crawling,
            on_charger=self.on_charger,
            bumper=self.drive.bumper,
            collisions=self.drive.collisions,
            odometer_m=self.drive.odometer,
            trail=self.trail_on,
        )

    def scan(self) -> list[float]:
        """The laser's ranges; `inf` where a ray hit nothing."""
        return sensors.scan(self.world, self.drive.x, self.drive.y, self.drive.heading)

    def ranges(self) -> dict[str, float]:
        """The range sensors' readings, by name."""
        return sensors.ranges(self.world, self.drive.x, self.drive.y, self.drive.heading)

    # --- The trail ----------------------------------------------------------

    def _clear_trail(self, on: bool) -> None:
        if self.trail or self.trail_on != on:
            self.trail_version += 1
        self.trail = []
        self.trail_on = on

    def _record_trail(self) -> None:
        here = (self.drive.x, self.drive.y)
        spacing = TRAIL_SPACING_M - DISTANCE_TOLERANCE_M
        if self.trail and math.dist(self.trail[-1], here) < spacing:
            return
        self.trail.append(here)
        del self.trail[:-TRAIL_MAX_POINTS]
        self.trail_version += 1

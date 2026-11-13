# SPDX-License-Identifier: Apache-2.0
"""Goals: a drive to a cell, a drive into a square, and a dock.

A goal is what one action call becomes in the core. The simulation accepts
every goal and answers it through its result: `reason` is one word from a
closed list, `message` one sentence that names the cell or the square.
There is no other way a goal ends, and none by which it does not.

    reached           the turtle stands on the target
    docked            dock: the turtle stands on the charging pad
    out_of_range      the goal names no cell, no square
    target_occupied   the target blocks, or the turtle does not fit on it
    unreachable       no path leads there
    stuck             the turtle got nowhere for five seconds
    preempted         another goal was accepted
    teleop_override   a velocity command other than zero arrived
    teleported        teleport moved the turtle
    reset             reset was called
    canceled          the caller cancelled
"""

import math
from dataclasses import dataclass

import numpy as np

from .constants import GRID_CELLS, STUCK_WINDOW_S
from .navigation import Cell, Navigator, Point, nearest_traversable, traversable_cells
from .world import (
    FIRST_SQUARE,
    KIND_NAMES,
    LAST_SQUARE,
    cell_centre,
    in_grid,
    square_bounds,
)

REASONS = (
    "reached",
    "docked",
    "out_of_range",
    "target_occupied",
    "unreachable",
    "stuck",
    "preempted",
    "teleop_override",
    "teleported",
    "reset",
    "canceled",
)
"""The closed list. A result's `reason` is one of these."""

SUCCEEDED = ("reached", "docked")
"""The reasons a goal succeeds with. `canceled` is cancelled, every other one aborted."""


@dataclass(frozen=True)
class DriveResult:
    """The result fields of DriveTo and DriveToSquare."""

    success: bool
    reason: str
    message: str
    cell_x: int
    cell_y: int
    distance_m: float
    duration_s: float


@dataclass(frozen=True)
class DriveFeedback:
    """The feedback fields of DriveTo and DriveToSquare."""

    phase: str
    cell_x: int
    cell_y: int
    remaining_m: float
    progress: float


@dataclass(frozen=True)
class DockResult:
    """The result fields of Dock."""

    success: bool
    reason: str
    message: str
    battery_percent: float
    duration_s: float


@dataclass(frozen=True)
class DockFeedback:
    """The feedback fields of Dock."""

    phase: str
    battery_percent: float
    remaining_m: float


class Goal:
    """A goal the simulation has accepted.

    The caller keeps it: `cancel()` asks it to end, `feedback()` is a
    snapshot of how far it is, and `result` is None until it has ended.
    The simulation calls the rest.
    """

    kind = ""
    """`drive_to`, `drive_to_square` or `dock`: the action it came through."""

    def __init__(self, sim):
        self._sim = sim
        self.result: DriveResult | DockResult | None = None
        self._navigator: Navigator | None = None
        self._cancelling = False
        self._began_at = sim.time
        self._odometer_at_start = sim.drive.odometer

    # --- For the caller -------------------------------------------------------

    def cancel(self) -> None:
        """Ask the goal to end. The turtle brakes, and once it stands the goal is `canceled`."""
        self._cancelling = True

    def feedback(self) -> DriveFeedback | DockFeedback:
        raise NotImplementedError

    # --- For the simulation ---------------------------------------------------

    def begin(self) -> None:
        """Look at what the goal asks for. One that asks for the impossible ends here."""
        refusal = self._refusal()
        if refusal is not None:
            self.end(*refusal)
        else:
            self._navigator = Navigator(self._sim.world, self._target_of)

    def advance(self) -> tuple[float, float]:
        """The velocity command for the step that follows. May end the goal."""
        drive = self._sim.drive
        if self._cancelling:
            if drive.linear == 0.0 and drive.angular == 0.0:
                self.end("canceled")
            return 0.0, 0.0
        return self._advance()

    def end(self, reason: str, message: str | None = None) -> None:
        """End the goal, with a reason from the list and its sentence."""
        if self.result is None:
            assert reason in REASONS, reason
            self.result = self._result(reason, message or self._sentence(reason))
            self._sim.goal_ended(self)

    def world_changed(self) -> None:
        if self._navigator is not None:
            self._navigator.world_changed()

    @property
    def path(self) -> tuple[Point, ...]:
        """The path the turtle is on, in metres. Empty until there is one."""
        return () if self._navigator is None else self._navigator.points

    # --- What the three kinds fill in -----------------------------------------

    def _refusal(self) -> tuple[str, str] | None:
        """A reason and a sentence if the goal cannot even start."""
        raise NotImplementedError

    def _target_of(self, passable: np.ndarray) -> Cell | None:
        """The cell to drive to, given the cells that are traversable now."""
        raise NotImplementedError

    def _advance(self) -> tuple[float, float]:
        command = self._navigator.control(self._sim.drive, self._sim.step_s)
        if self._navigator.outcome is not None:
            self.end(self._navigator.outcome)
        return command

    def _result(self, reason: str, message: str) -> DriveResult | DockResult:
        raise NotImplementedError

    def _target(self) -> str:
        """How a sentence names where the goal leads."""
        raise NotImplementedError

    def _doing(self) -> str:
        """How a sentence names what the goal was at when something ended it."""
        return f"on the way to {self._target()}"

    def _sentence(self, reason: str) -> str:
        x, y = self._sim.cell
        return {
            "unreachable": f"No path leads from cell ({x}, {y}) to {self._target()}.",
            "stuck": f"The turtle made no progress for {STUCK_WINDOW_S:g} s {self._doing()}.",
            "preempted": f"Another goal was accepted {self._doing()}.",
            "teleop_override": f"A velocity command took over {self._doing()}.",
            "teleported": f"The turtle was teleported {self._doing()}.",
            "reset": f"The simulator was reset {self._doing()}.",
            "canceled": f"The goal was cancelled {self._doing()}.",
        }[reason]

    # --- Shared by the kinds --------------------------------------------------

    def _seconds(self) -> float:
        return self._sim.time - self._began_at

    def _driven(self) -> float:
        return self._sim.drive.odometer - self._odometer_at_start

    def _remaining(self, target: Cell | None) -> float:
        """Metres left: along the path while there is one, else straight to the target."""
        drive = self._sim.drive
        if self._navigator is not None and self._navigator.phase == "driving":
            return self._navigator.remaining(drive.x, drive.y)
        if target is None:
            return 0.0
        return math.dist((drive.x, drive.y), cell_centre(*target))

    def _why_occupied(self, cell: Cell) -> str:
        """Why a cell is no target: it blocks, or the turtle does not fit on it."""
        x, y = cell
        if self._sim.world.blocks(x, y):
            return f"Cell ({x}, {y}) is {KIND_NAMES[self._sim.world.kind(x, y)]}."
        return f"The turtle does not fit on cell ({x}, {y})."


class _Drive(Goal):
    """What the two drive goals share: their result and their feedback."""

    _cell: Cell | None = None  # the cell the drive leads to, once it is known

    def feedback(self) -> DriveFeedback:
        x, y = self._sim.cell
        remaining = self._remaining(self._cell)
        driven = self._driven()
        return DriveFeedback(
            phase="planning" if self._navigator is None else self._navigator.phase,
            cell_x=x,
            cell_y=y,
            remaining_m=remaining,
            progress=driven / (driven + remaining) if driven + remaining > 0.0 else 1.0,
        )

    def _result(self, reason: str, message: str) -> DriveResult:
        x, y = self._sim.cell
        return DriveResult(
            success=reason in SUCCEEDED,
            reason=reason,
            message=message,
            cell_x=x,
            cell_y=y,
            distance_m=self._driven(),
            duration_s=self._seconds(),
        )


class DriveTo(_Drive):
    """To the centre of a cell."""

    kind = "drive_to"

    def __init__(self, sim, x: int, y: int):
        super().__init__(sim)
        self._cell = (x, y)

    def _refusal(self) -> tuple[str, str] | None:
        x, y = self._cell
        if not in_grid(x, y):
            return "out_of_range", f"Cell ({x}, {y}) is outside the grid, 0 to {GRID_CELLS - 1}."
        if not self._sim.world.traversable(x, y):
            return "target_occupied", self._why_occupied(self._cell)
        return None

    def _target_of(self, passable: np.ndarray) -> Cell | None:
        x, y = self._cell
        return self._cell if passable[y, x] else None

    def _target(self) -> str:
        return "cell ({}, {})".format(*self._cell)

    def _sentence(self, reason: str) -> str:
        if reason == "reached":
            return f"The turtle reached {self._target()}."
        if reason == "target_occupied":
            return self._why_occupied(self._cell)
        return super()._sentence(reason)


class DriveToSquare(_Drive):
    """Into a square: to the traversable cell nearest to the square's centre, inside it."""

    kind = "drive_to_square"

    def __init__(self, sim, square: str):
        super().__init__(sim)
        self._square = square
        self._bounds = square_bounds(square)

    def _refusal(self) -> tuple[str, str] | None:
        if self._bounds is None:
            # Whatever was sent, the sentence stays one line that can be read.
            name = self._square if len(self._square) <= 16 else self._square[:16] + "..."
            name = "".join(letter if letter.isprintable() else "?" for letter in name)
            return "out_of_range", (
                f'"{name}" names no square: they run from {FIRST_SQUARE} to {LAST_SQUARE}.'
            )
        if self._target_of(traversable_cells(self._sim.world)) is None:
            return "target_occupied", self._sentence("target_occupied")
        return None

    def _target_of(self, passable: np.ndarray) -> Cell | None:
        x_min, y_min, x_max, y_max = self._bounds
        # The square's centre, in cells: the corner its four middle cells share.
        centre_x, centre_y = (x_min + x_max + 1) / 2, (y_min + y_max + 1) / 2
        self._cell = nearest_traversable(passable, centre_x, centre_y, self._bounds)
        return self._cell

    def _target(self) -> str:
        return f"square {self._square}"

    def _sentence(self, reason: str) -> str:
        if reason == "reached":
            return "The turtle reached {} at cell ({}, {}).".format(self._target(), *self._cell)
        if reason == "target_occupied":
            return f"Square {self._square} has no cell the turtle fits on."
        return super()._sentence(reason)


class Dock(Goal):
    """To the start cell on the charging pad. There the battery charges, with or without a goal."""

    kind = "dock"

    def feedback(self) -> DockFeedback:
        return DockFeedback(
            phase="planning" if self._navigator is None else self._navigator.phase,
            battery_percent=self._sim.battery.percent,
            remaining_m=self._remaining(self._sim.world.start_cell),
        )

    def _refusal(self) -> tuple[str, str] | None:
        if not self._sim.world.traversable(*self._sim.world.start_cell):
            return "target_occupied", self._sentence("target_occupied")
        return None

    def _target_of(self, passable: np.ndarray) -> Cell | None:
        x, y = self._sim.world.start_cell
        return (x, y) if passable[y, x] else None

    def _advance(self) -> tuple[float, float]:
        command = self._navigator.control(self._sim.drive, self._sim.step_s)
        outcome = self._navigator.outcome
        if outcome is not None:
            # On the start cell the dock is done. What the pad does then is the battery's.
            self.end("docked" if outcome == "reached" else outcome)
        return command

    def _result(self, reason: str, message: str) -> DockResult:
        return DockResult(
            success=reason in SUCCEEDED,
            reason=reason,
            message=message,
            battery_percent=self._sim.battery.percent,
            duration_s=self._seconds(),
        )

    def _target(self) -> str:
        return "the charging pad"

    def _sentence(self, reason: str) -> str:
        if reason == "docked":
            return "The turtle stands on the charging pad."
        if reason == "target_occupied":
            x, y = self._sim.world.start_cell
            return f"The turtle does not fit on the charging pad's cell ({x}, {y})."
        return super()._sentence(reason)

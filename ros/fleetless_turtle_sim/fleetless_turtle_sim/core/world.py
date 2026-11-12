# SPDX-License-Identifier: Apache-2.0
"""The grid world: which cell is what, and where a circle fits.

Cells are addressed as (cx, cy) from 0 to 255 with the origin bottom-left;
x points east, y north. `kinds[cy, cx]` holds a cell's kind, so row 0 of the
array is the south edge. Metres are in the `map` frame, whose origin is the
outer corner of cell (0, 0).
"""

import math
import re
from pathlib import Path

import cv2
import numpy as np

from .constants import (
    BLOCKING,
    CELL_SIZE_M,
    GRID_CELLS,
    MAP_COLOURS,
    PAD,
    PLANNER_CLEARANCE_M,
    SQUARE_CELLS,
    SQUARES,
    TOUCH_TOLERANCE_M2,
    TURTLE_RADIUS_M,
)

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:SQUARES]
_SQUARE = re.compile(rf"([{_LETTERS[0]}-{_LETTERS[-1]}])([1-9][0-9]?)")


class MapError(Exception):
    """A map the simulator refuses. The message is one sentence for the user."""


# --- Coordinates ------------------------------------------------------------


def in_grid(cx: int, cy: int) -> bool:
    return 0 <= cx < GRID_CELLS and 0 <= cy < GRID_CELLS


def cell_of(x: float, y: float) -> tuple[int, int]:
    """The cell that holds the point (x, y), in metres. It may lie outside the grid."""
    return math.floor(x / CELL_SIZE_M), math.floor(y / CELL_SIZE_M)


def cell_centre(cx: int, cy: int) -> tuple[float, float]:
    """The centre of a cell, in metres."""
    return (cx + 0.5) * CELL_SIZE_M, (cy + 0.5) * CELL_SIZE_M


def square_of(cx: int, cy: int) -> str:
    """The square a cell lies in: the letter follows x, the number y, as on a chess board."""
    if not in_grid(cx, cy):
        raise ValueError(f"Cell ({cx}, {cy}) is outside the grid.")
    return f"{_LETTERS[cx // SQUARE_CELLS]}{cy // SQUARE_CELLS + 1}"


def square_bounds(square: str) -> tuple[int, int, int, int] | None:
    """A square's cells as (x_min, y_min, x_max, y_max), or None if the name is no square."""
    # fullmatch: `$` would let a trailing newline through.
    match = _SQUARE.fullmatch(square)
    if match is None or not 1 <= int(match[2]) <= SQUARES:
        return None
    x_min = _LETTERS.index(match[1]) * SQUARE_CELLS
    y_min = (int(match[2]) - 1) * SQUARE_CELLS
    return x_min, y_min, x_min + SQUARE_CELLS - 1, y_min + SQUARE_CELLS - 1


# --- The world --------------------------------------------------------------


class World:
    """The cells, as loaded, and as they are now."""

    def __init__(self, kinds: np.ndarray, name: str = "The map"):
        """`kinds` is indexed [cy, cx]; `name` is how a refusal names the map."""
        if kinds.shape != (GRID_CELLS, GRID_CELLS):
            height, width = kinds.shape[:2]
            raise MapError(
                f"{name} is {width} x {height} cells; a map is exactly "
                f"{GRID_CELLS} x {GRID_CELLS}."
            )
        self.kinds = kinds.astype(np.uint8)
        self._loaded = self.kinds.copy()
        self._blocking = np.isin(self.kinds, BLOCKING)
        if not (self.kinds == PAD).any():
            raise MapError(f"{name} has no charging pad (#00ff00).")
        start = self._find_start()
        if start is None:
            raise MapError(f"{name} has a charging pad, but no pad cell the turtle fits on.")
        self.start_cell = start

    @classmethod
    def load(cls, path: str | Path) -> "World":
        """Read a map file: an 8-bit RGB PNG, one pixel per cell, north up."""
        path = Path(path)
        if not path.is_file():
            raise MapError(f"Map {path} does not exist.")
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise MapError(f"Map {path} is not an image this simulator can read; use a PNG.")
        if image.dtype != np.uint8:
            raise MapError(
                f"Map {path} has {image.dtype.itemsize * 8}-bit channels; a map is an 8-bit PNG."
            )
        height, width = image.shape[:2]
        if (width, height) != (GRID_CELLS, GRID_CELLS):
            raise MapError(
                f"Map {path} is {width} x {height} pixels; a map is exactly "
                f"{GRID_CELLS} x {GRID_CELLS}."
            )
        return cls(_kinds_of_image(image, path), name=f"Map {path}")

    # --- Cells --------------------------------------------------------------

    def kind(self, cx: int, cy: int) -> int:
        return int(self.kinds[cy, cx])

    def blocks(self, cx: int, cy: int) -> bool:
        """Wall, rock and water block; so does everything outside the grid."""
        return not in_grid(cx, cy) or bool(self._blocking[cy, cx])

    def is_pad(self, cx: int, cy: int) -> bool:
        return in_grid(cx, cy) and self.kinds[cy, cx] == PAD

    def set_kind(self, cx: int, cy: int, kind: int) -> None:
        self.kinds[cy, cx] = kind
        self._blocking[cy, cx] = kind in BLOCKING

    def restore(self) -> None:
        """Back to the map as loaded."""
        self.kinds[:] = self._loaded
        self._blocking = np.isin(self.kinds, BLOCKING)

    # --- Where a circle fits ------------------------------------------------

    def touches(self, x: float, y: float, radius: float) -> bool:
        """Whether a circle at (x, y) touches a blocking cell or leaves the grid."""
        limit = radius * radius - TOUCH_TOLERANCE_M2
        cx_min, cy_min = cell_of(x - radius, y - radius)
        cx_max, cy_max = cell_of(x + radius, y + radius)
        for cy in range(cy_min, cy_max + 1):
            # The point of the cell nearest to the circle's centre decides.
            dy = y - min(max(y, cy * CELL_SIZE_M), (cy + 1) * CELL_SIZE_M)
            for cx in range(cx_min, cx_max + 1):
                if not self.blocks(cx, cy):
                    continue
                dx = x - min(max(x, cx * CELL_SIZE_M), (cx + 1) * CELL_SIZE_M)
                if dx * dx + dy * dy < limit:
                    return True
        return False

    def fits(self, x: float, y: float) -> bool:
        """Whether the turtle's footprint is a valid pose at (x, y)."""
        return not self.touches(x, y, TURTLE_RADIUS_M)

    def traversable(self, cx: int, cy: int) -> bool:
        """Whether the turtle fits on the cell's centre with the planner's clearance to spare."""
        if not in_grid(cx, cy):
            return False
        x, y = cell_centre(cx, cy)
        return not self.touches(x, y, TURTLE_RADIUS_M + PLANNER_CLEARANCE_M)

    # --- The start ----------------------------------------------------------

    @property
    def start_pose(self) -> tuple[float, float, float]:
        """Where the turtle starts and where reset returns it: x, y, heading (east)."""
        x, y = cell_centre(*self.start_cell)
        return x, y, 0.0

    def _find_start(self) -> tuple[int, int] | None:
        """The traversable pad cell nearest to the pad's centroid."""
        cys, cxs = np.nonzero(self.kinds == PAD)
        centre_x, centre_y = cxs.mean(), cys.mean()
        best = None
        for cx, cy in zip(cxs.tolist(), cys.tolist(), strict=True):
            if not self.traversable(cx, cy):
                continue
            # Ties go to the lower row, then the lower column, so the answer
            # never depends on the order numpy lists the cells in.
            key = ((cx - centre_x) ** 2 + (cy - centre_y) ** 2, cy, cx)
            if best is None or key < best[0]:
                best = (key, (cx, cy))
        return None if best is None else best[1]


def _kinds_of_image(image: np.ndarray, path: Path) -> np.ndarray:
    """Turn decoded pixels (OpenCV's blue-green-red order, north up) into `kinds`."""
    if image.ndim == 2:
        # A greyscale file: every channel carries the same value.
        image = np.repeat(image[:, :, None], 3, axis=2)
    blue, green, red = (image[:, :, i].astype(np.uint32) for i in range(3))
    # A fully opaque alpha channel is what many paint programs save; it
    # changes no colour. Anything else is a colour of its own.
    alpha = image[:, :, 3] if image.shape[2] == 4 else np.full(image.shape[:2], 0xFF, np.uint8)
    code = (red << 16) | (green << 8) | blue

    kinds = np.full(image.shape[:2], 0xFF, np.uint8)
    for (r, g, b), kind in MAP_COLOURS.items():
        kinds[(code == ((r << 16) | (g << 8) | b)) & (alpha == 0xFF)] = kind

    rows, columns = np.nonzero(kinds == 0xFF)
    if rows.size:
        row, column = int(rows[0]), int(columns[0])
        colour = f"#{int(code[row, column]):06x}"
        if alpha[row, column] != 0xFF:
            colour += f"{int(alpha[row, column]):02x}"
        allowed = ", ".join(f"#{r:02x}{g:02x}{b:02x}" for r, g, b in MAP_COLOURS)
        raise MapError(
            f"Map {path} has a pixel of colour {colour} at column {column}, row {row} "
            f"from the top left, which is none of {allowed} (anti-aliasing is the usual cause)."
        )
    # The file is north up; row 0 of `kinds` is the south edge.
    return np.flipud(kinds).copy()

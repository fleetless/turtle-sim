# SPDX-License-Identifier: Apache-2.0
"""What a camera is told before it draws: the world and the turtle in it."""

from dataclasses import dataclass, field

import numpy as np


def _no_points() -> np.ndarray:
    return np.empty((0, 2))


@dataclass(frozen=True)
class Scene:
    """One moment of the world. Metres are in the `map` frame."""

    kinds: np.ndarray
    """Every cell's kind as `kinds[cy, cx]`, row 0 the south edge: the
    values of fleetless_turtle_msgs/msg/World."""

    x: float
    y: float
    """The turtle's centre."""

    heading: float
    """Radians, 0 is east, counter-clockwise positive."""

    plan: np.ndarray = field(default_factory=_no_points)
    """The planned path as rows of (x, y); the last one is the target.
    Empty without a goal."""

    trail: np.ndarray = field(default_factory=_no_points)
    """The recorded trail as rows of (x, y), oldest first. Empty while off."""


class Remembered:
    """The last result of a slow function of the cells, kept until a cell changes.

    The world changes when somebody puts a rock down, which is rare; the
    turtle moves ten pictures a second. So what depends on the cells alone
    is drawn once and looked up after. The answer is the same either way:
    a renderer stays a function of its scene.
    """

    def __init__(self, build):
        self._build = build
        self._kinds = None
        self._result = None

    def __call__(self, kinds: np.ndarray):
        if self._kinds is None or not np.array_equal(self._kinds, kinds):
            self._result = self._build(kinds)
            # A copy: the caller may write to its own array afterwards.
            self._kinds = kinds.copy()
        return self._result

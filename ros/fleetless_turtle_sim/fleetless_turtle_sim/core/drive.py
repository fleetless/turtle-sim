# SPDX-License-Identifier: Apache-2.0
"""The drive: a differential drive as a unicycle, one fixed step at a time.

A velocity command names a linear and an angular speed. The drive clamps
them to its limits, approaches them under its acceleration limits and moves
the pose: the rotation always, the translation only where the turtle fits.
There is no sliding along walls.
"""

import math

from .constants import (
    ANGULAR_ACCELERATION_LIMIT,
    ANGULAR_SPEED_LIMIT,
    BUMPER_HOLD_S,
    COMMAND_TIMEOUT_S,
    CRAWL_ANGULAR_FACTOR,
    CRAWL_LINEAR_FACTOR,
    LINEAR_ACCELERATION_LIMIT,
    LINEAR_SPEED_LIMIT,
    TIME_TOLERANCE_S,
)
from .world import World


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _approach(value: float, target: float, most: float) -> float:
    """`value` moved towards `target` by at most `most`."""
    return value + _clamp(target - value, most)


class Drive:
    """The turtle's pose and speeds."""

    def __init__(self, world: World, x: float, y: float, heading: float):
        self.world = world
        self.x = x
        self.y = y
        self.heading = heading
        """Radians, 0 is east, counter-clockwise positive, in (-pi, pi]."""
        self.linear = 0.0
        """m/s along the heading; negative is backwards."""
        self.angular = 0.0
        """rad/s, counter-clockwise positive."""
        self.bumper = False
        """True while a move was refused within the last 0.2 s."""
        self.collisions = 0
        """How often the bumper went from false to true."""
        self.odometer = 0.0
        """Metres driven, forwards and backwards alike."""
        self.non_finite_commands = 0
        """Commands that carried a NaN or an infinity, which counted as 0."""
        self._command = (0.0, 0.0)
        self._since_command = math.inf
        self._since_refusal = math.inf

    def command(self, linear: float, angular: float) -> None:
        """Take a velocity command: `linear.x` and `angular.z` of a Twist."""
        if not (math.isfinite(linear) and math.isfinite(angular)):
            self.non_finite_commands += 1
            linear = linear if math.isfinite(linear) else 0.0
            angular = angular if math.isfinite(angular) else 0.0
        self._command = (linear, angular)
        self._since_command = 0.0

    def place(self, x: float, y: float, heading: float) -> None:
        """Put the turtle somewhere, standing. The caller has checked that it fits."""
        self.x = x
        self.y = y
        self.heading = math.atan2(math.sin(heading), math.cos(heading))
        self.linear = 0.0
        self.angular = 0.0
        self._command = (0.0, 0.0)

    def step(self, dt: float, crawling: bool = False) -> None:
        """Advance by `dt` seconds. `crawling` reduces both speed limits."""
        # The robot-side stop: a command is good for half a second.
        target_linear, target_angular = (0.0, 0.0)
        if self._since_command < COMMAND_TIMEOUT_S - TIME_TOLERANCE_S:
            target_linear, target_angular = self._command
        self._since_command += dt

        linear_limit = LINEAR_SPEED_LIMIT * (CRAWL_LINEAR_FACTOR if crawling else 1.0)
        angular_limit = ANGULAR_SPEED_LIMIT * (CRAWL_ANGULAR_FACTOR if crawling else 1.0)
        self.linear = _approach(
            self.linear, _clamp(target_linear, linear_limit), LINEAR_ACCELERATION_LIMIT * dt
        )
        self.angular = _approach(
            self.angular, _clamp(target_angular, angular_limit), ANGULAR_ACCELERATION_LIMIT * dt
        )

        # The rotation always: a circle turns wherever it fits.
        heading = self.heading + self.angular * dt
        self.heading = math.atan2(math.sin(heading), math.cos(heading))

        # The translation only into a valid pose.
        self._since_refusal += dt
        distance = self.linear * dt
        if distance != 0.0:
            x = self.x + distance * math.cos(self.heading)
            y = self.y + distance * math.sin(self.heading)
            if self.world.fits(x, y):
                self.x = x
                self.y = y
                self.odometer += abs(distance)
            else:
                self.linear = 0.0
                self._since_refusal = 0.0

        bumper = self._since_refusal < BUMPER_HOLD_S - TIME_TOLERANCE_S
        if bumper and not self.bumper:
            self.collisions += 1
        self.bumper = bumper

# SPDX-License-Identifier: Apache-2.0
"""How many fixed steps are due, given the time.

The simulation only knows steps. Whoever drives it has a clock, and a timer
that is sometimes late: on a busy machine, fifty calls a second become forty.
One step per call would then run the world at four fifths of its speed. So
the caller asks here, with the time it reads itself, and takes as many steps
as are due.

Only a short lag is made up. After a long one (a slow plan, a machine that
slept) the world has stood still for that while, and it goes on from where
it is. It never jumps.
"""

from .constants import CATCH_UP_STEPS, STEP_S


class Pace:
    def __init__(self, step_s: float = STEP_S, most: int = CATCH_UP_STEPS):
        self._step_s = step_s
        self._most = most
        self._taken = 0
        self._origin = None

    def due(self, now_s: float) -> int:
        """The steps to take at `now_s`, seconds on any steady clock. Often 1, sometimes 0."""
        if self._origin is None:
            self._origin = now_s
        # The first step is due at the origin, the next one a step later.
        steps = int((now_s - self._origin) / self._step_s) + 1 - self._taken
        if steps > self._most:
            # Too far behind to make up. Time starts again here.
            self._origin = now_s - self._taken * self._step_s
            steps = 1
        steps = max(steps, 0)
        self._taken += steps
        return steps

# SPDX-License-Identifier: Apache-2.0
"""The battery: it empties by standing, driving and turning, and fills on the pad.

Empty, it does not stop the turtle. It makes it crawl, until the battery is
back at 5 %.
"""

import math

from .constants import (
    ANGULAR_SPEED_LIMIT,
    BATTERY_CHARGING_S,
    BATTERY_DRIVING_S,
    BATTERY_STANDING_S,
    BATTERY_TURNING_S,
    BATTERY_VOLTAGE_EMPTY,
    BATTERY_VOLTAGE_FULL,
    CRAWL_END_PERCENT,
    CRAWL_START_PERCENT,
    LINEAR_SPEED_LIMIT,
)

FULL = 100.0

# Percent per second. Driving and turning are what full speed costs beyond
# standing, so the three parts add up.
_STANDING = FULL / BATTERY_STANDING_S
_DRIVING = FULL / BATTERY_DRIVING_S - _STANDING
_TURNING = FULL / BATTERY_TURNING_S - _STANDING
_CHARGING = FULL / BATTERY_CHARGING_S


class Battery:
    def __init__(self, drain: float = 1.0):
        """`drain` multiplies the three discharge rates; 0 turns discharge off."""
        if not math.isfinite(drain) or drain < 0.0:
            raise ValueError(f"A drain factor of {drain} is not a number of 0 or more.")
        self.drain = drain
        self.percent = FULL
        self.crawling = False

    def step(self, dt: float, linear: float, angular: float, on_pad: bool) -> None:
        """Advance by `dt` seconds at the given speeds."""
        if on_pad:
            # On the pad the battery charges whether the turtle moves or not.
            self.percent += _CHARGING * dt
        else:
            rate = (
                _STANDING
                + _DRIVING * min(abs(linear) / LINEAR_SPEED_LIMIT, 1.0)
                + _TURNING * min(abs(angular) / ANGULAR_SPEED_LIMIT, 1.0)
            )
            self.percent -= rate * self.drain * dt
        self.percent = max(0.0, min(FULL, self.percent))
        if self.percent <= CRAWL_START_PERCENT:
            self.crawling = True
        elif self.percent >= CRAWL_END_PERCENT:
            self.crawling = False

    def refill(self) -> None:
        self.percent = FULL
        self.crawling = False

    @property
    def voltage(self) -> float:
        """Volts: linear from empty to full."""
        share = self.percent / FULL
        return BATTERY_VOLTAGE_EMPTY + (BATTERY_VOLTAGE_FULL - BATTERY_VOLTAGE_EMPTY) * share

    def supply(self, on_pad: bool) -> str:
        """`charging` on the pad below 100 %, `full` on the pad at 100 %, else `discharging`."""
        if not on_pad:
            return "discharging"
        return "full" if self.percent >= FULL else "charging"

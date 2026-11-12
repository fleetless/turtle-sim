# SPDX-License-Identifier: Apache-2.0
"""The model's joints: four legs and a head, moved by how the turtle drives.

The turtle has no legs to drive with: it is a circle on two wheels. The
legs are what a watcher sees of the wheels. Each side's legs swing with the
distance that side's wheel has rolled, front and hind against each other,
so a turtle that turns on the spot walks one side backwards. The head turns
into a curve.

The names are the URDF's, in `fleetless_turtle_description`. A positive leg
angle swings that leg forwards, on either side.
"""

import math

from .constants import ANGULAR_SPEED_LIMIT, HEAD_TURN_LIMIT, LEG_STRIDE_M, LEG_SWING

HEAD = "head_joint"
FRONT_LEFT = "leg_front_left_joint"
FRONT_RIGHT = "leg_front_right_joint"
HIND_LEFT = "leg_hind_left_joint"
HIND_RIGHT = "leg_hind_right_joint"

NAMES = (HEAD, FRONT_LEFT, FRONT_RIGHT, HIND_LEFT, HIND_RIGHT)
"""Every joint, in the order `/joint_states` lists them."""


def positions(left_wheel_m: float, right_wheel_m: float, angular: float) -> dict[str, float]:
    """Every joint's angle in radians, by name.

    `left_wheel_m` and `right_wheel_m` are how far each side's wheel has
    rolled, `angular` the turn rate in rad/s, counter-clockwise positive.
    """
    left = LEG_SWING * math.sin(2.0 * math.pi * left_wheel_m / LEG_STRIDE_M)
    right = LEG_SWING * math.sin(2.0 * math.pi * right_wheel_m / LEG_STRIDE_M)
    turn = max(-1.0, min(1.0, angular / ANGULAR_SPEED_LIMIT))
    return {
        HEAD: HEAD_TURN_LIMIT * turn,
        # The right side is half a stride ahead of the left: driving straight,
        # a front leg swings with the hind leg across from it, as a turtle walks.
        FRONT_LEFT: left,
        FRONT_RIGHT: -right,
        HIND_LEFT: -left,
        HIND_RIGHT: right,
    }

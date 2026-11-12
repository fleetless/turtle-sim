# SPDX-License-Identifier: Apache-2.0
# shellcheck shell=bash
# The turtle's ROS environment, for bash to source: ROS itself, the
# simulator's packages and, if start.sh built one, the bridge from
# /bridge-src. start.sh, the `ros2` wrapper and the user's shell all read
# this one file, so they all see the same graph.

# shellcheck source=/dev/null
source "/opt/ros/$ROS_DISTRO/setup.bash"
# shellcheck source=/dev/null
source /opt/turtle/install/setup.bash

# Where start.sh builds a mounted bridge checkout. Sourced last, so it wins
# over the installed package.
TURTLE_BRIDGE_OVERLAY=/tmp/turtle-bridge
if [ -f "$TURTLE_BRIDGE_OVERLAY/install/local_setup.bash" ]; then
    # shellcheck source=/dev/null
    source "$TURTLE_BRIDGE_OVERLAY/install/local_setup.bash"
fi

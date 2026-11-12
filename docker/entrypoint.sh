#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# What the container runs. Without arguments: the launch file. With
# arguments: that command, in the turtle's ROS environment.
#
# A bridge checkout mounted at /bridge-src is built first and runs in place
# of the installed package. That is how an unreleased bridge is tried:
#
#   docker run ... -v /path/to/bridge:/bridge-src:ro ghcr.io/fleetless/turtle-sim
set -eo pipefail

BRIDGE_SRC=/bridge-src

if [ -e "$BRIDGE_SRC" ]; then
    if [ ! -f "$BRIDGE_SRC/package.xml" ]; then
        echo "entrypoint: $BRIDGE_SRC holds no package.xml. Mount a checkout of the bridge there." >&2
        exit 1
    fi
    # shellcheck source=/dev/null
    source "/opt/ros/$ROS_DISTRO/setup.bash"
    installed="$(dpkg-query -W -f='${Version}' "ros-$ROS_DISTRO-fleetless-bridge" 2> /dev/null || echo "none")"
    echo "entrypoint: building the bridge from $BRIDGE_SRC. It replaces the installed ros-$ROS_DISTRO-fleetless-bridge $installed."

    # A copy, because the mount is read-only and a build writes next to its
    # source. The same path as in environment.sh.
    overlay=/tmp/turtle-bridge
    rm -rf "$overlay"
    mkdir -p "$overlay/src/fleetless_bridge"
    tar -C "$BRIDGE_SRC" --exclude=./.git --exclude=./build --exclude=./install --exclude=./log -cf - . \
        | tar -C "$overlay/src/fleetless_bridge" -xf -
    if ! (cd "$overlay" && colcon build --base-paths src --event-handlers console_direct+); then
        echo "entrypoint: the bridge from $BRIDGE_SRC did not build. The installed bridge is not started in its place." >&2
        exit 1
    fi
fi

# shellcheck source=/dev/null
source /opt/turtle/environment.sh

if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec ros2 launch fleetless_turtle_bringup turtle.launch.py

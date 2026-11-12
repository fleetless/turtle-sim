#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Sets up the turtle's ROS environment and runs one thing in it: the command
# it was given or, without one, the turtle. Only the entrypoint calls it: it
# starts this script for the turtle, and reads it into its own shell for a
# command.
#
# A bridge checkout mounted at /bridge-src is built first and runs in place
# of the installed package. That is how an unreleased bridge is tried:
#
#   docker run ... -v /path/to/bridge:/bridge-src:ro ghcr.io/fleetless/turtle-sim
#
# A stop while this script runs. For the turtle, the entrypoint watches: it
# sends SIGUSR1, which this shell does not catch, so it ends here and now,
# and the container with it; a build of /bridge-src that is under way is
# not waited for. With a command, these lines run in the container's first
# process until the command takes over, and the trap below is what hears a
# stop; a build of /bridge-src is then finished first, which may take longer
# than Docker waits.
set -eo pipefail
trap 'exit 130' INT TERM

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
exec python3 /opt/turtle/launcher.py

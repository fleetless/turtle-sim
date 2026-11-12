#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Runs inside the image of docker/ros.Dockerfile, started by
# `./run-tests.sh ros`: copies the mounted source into a workspace, builds it
# with colcon and runs every package's tests.
set -euo pipefail

SRC=/src
WS=/ws

# A copy, because the source is mounted read-only and colcon writes next to
# what it builds. `cp -a` keeps the timestamps, so a workspace kept between
# runs (TURTLE_TEST_WS) rebuilds only what changed.
rm -rf "$WS/src"
mkdir -p "$WS/src/turtle-sim"
for part in ros maps fleetless; do
    if [ -e "$SRC/$part" ]; then cp -a "$SRC/$part" "$WS/src/turtle-sim/"; fi
done

cd "$WS"
# The setup files read variables they do not set first.
set +u
# shellcheck source=/dev/null
source "/opt/ros/$ROS_DISTRO/setup.bash"
set -u

colcon build --base-paths src/turtle-sim/ros --event-handlers console_cohesion+

set +u
# shellcheck source=/dev/null
source install/setup.bash
set -u

# Nothing outside this container may answer a test.
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST

colcon test --base-paths src/turtle-sim/ros --event-handlers console_direct+ --return-code-on-test-failure
colcon test-result --verbose

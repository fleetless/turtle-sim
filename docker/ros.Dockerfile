# SPDX-License-Identifier: Apache-2.0
# The image behind `./run-tests.sh ros`: ROS 2 Jazzy plus whatever the
# packages' manifests name. Only the manifests are copied, so the layer is
# rebuilt when a dependency changes and not when a line of code does; the
# source itself is mounted by run-tests.sh.
FROM ros:jazzy-ros-base
COPY ros/fleetless_turtle_msgs/package.xml /manifests/fleetless_turtle_msgs/package.xml
COPY ros/fleetless_turtle_sim/package.xml /manifests/fleetless_turtle_sim/package.xml
COPY ros/fleetless_turtle_viewer/package.xml /manifests/fleetless_turtle_viewer/package.xml
COPY ros/fleetless_turtle_bringup/package.xml /manifests/fleetless_turtle_bringup/package.xml
# The bridge is no rosdep key: the real image installs it from Fleetless's
# apt repository, and no test here needs it.
RUN apt-get update \
 && rosdep update --rosdistro "$ROS_DISTRO" \
 && rosdep install --from-paths /manifests --ignore-src -y --skip-keys fleetless_bridge \
 && rm -rf /var/lib/apt/lists/*

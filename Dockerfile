# SPDX-License-Identifier: Apache-2.0
# The turtle in one image: ROS 2 Jazzy, the simulator, the view and the
# Fleetless Bridge.
#
#   docker build -t turtle-sim .
#
# The bridge is one file: the .deb of the bridge's GitHub release, pinned by
# its version and by its SHA-256. Fleetless's apt repository serves the same
# file, but only of the newest bridge, so a pin on an older one would stop
# building the day a newer one is published. A release keeps its files. The
# bridge-bump workflow rewrites these two lines when apt serves a newer
# bridge.
ARG BRIDGE_VERSION=6.5.0-0noble
ARG BRIDGE_DEB_SHA256=0573a9545b432b3680266c07adc0aa6529f56644d49cf1bd3d41dc98b72734a6

# --- What every stage needs: ROS, and what the packages' manifests name ------
FROM ros:jazzy-ros-base AS base
# Only the manifests, so this layer is rebuilt when a dependency changes and
# not when a line of code does. ROS apt packages are not pinned to versions:
# packages.ros.org keeps no old ones.
COPY ros/fleetless_turtle_msgs/package.xml /tmp/manifests/fleetless_turtle_msgs/package.xml
COPY ros/fleetless_turtle_sim/package.xml /tmp/manifests/fleetless_turtle_sim/package.xml
COPY ros/fleetless_turtle_viewer/package.xml /tmp/manifests/fleetless_turtle_viewer/package.xml
COPY ros/fleetless_turtle_cameras/package.xml /tmp/manifests/fleetless_turtle_cameras/package.xml
COPY ros/fleetless_turtle_description/package.xml /tmp/manifests/fleetless_turtle_description/package.xml
COPY ros/fleetless_turtle_bringup/package.xml /tmp/manifests/fleetless_turtle_bringup/package.xml
# Everything but the test dependencies. The bridge is no rosdep key; it is
# installed below.
RUN apt-get update \
 && rosdep update --rosdistro "$ROS_DISTRO" \
 && rosdep install --from-paths /tmp/manifests --ignore-src -y \
        --dependency-types buildtool --dependency-types buildtool_export \
        --dependency-types build --dependency-types build_export \
        --dependency-types exec \
        --skip-keys fleetless_bridge \
 && rm -rf /var/lib/apt/lists/* /tmp/manifests

# --- The packages, built ------------------------------------------------------
FROM base AS build
COPY ros /opt/turtle/src
RUN . "/opt/ros/$ROS_DISTRO/setup.sh" \
 && cd /opt/turtle \
 && colcon build --base-paths src --cmake-args -DBUILD_TESTING=OFF \
 && rm -rf build log

# --- The image ------------------------------------------------------------------
FROM base
ARG BRIDGE_VERSION
ARG BRIDGE_DEB_SHA256
ARG VERSION=0.0.0
ARG REVISION=unknown

# The bridge: the pinned file from the bridge's release, installed only if
# its sum is the pinned one. The release's tag is the version up to its last
# hyphen. apt installs the file and, from the sources the base image has,
# what it depends on. A download that fails ends the build with curl's own
# error; there is no second place to ask.
RUN deb="ros-$ROS_DISTRO-fleetless-bridge_${BRIDGE_VERSION}_all.deb" \
 && dir="$(mktemp -d)" \
 && apt-get update \
 && apt-get install -y curl ca-certificates \
 && curl -fsSL -o "$dir/$deb" \
      "https://github.com/fleetless/bridge/releases/download/v${BRIDGE_VERSION%-*}/$deb" \
 && { echo "$BRIDGE_DEB_SHA256  $dir/$deb" | sha256sum -c --strict --status - 2> /dev/null \
      || { echo "The bridge's .deb is not the one this Dockerfile pins: expected $BRIDGE_DEB_SHA256, got $(sha256sum "$dir/$deb" | cut -d' ' -f1)." >&2; exit 1; }; } \
 && apt-get install -y "$dir/$deb" \
 && rm -rf "$dir" /var/lib/apt/lists/*

# A user that is not root, and a place for what outlives the container: the
# bridge keeps there which goal belongs to which job. The base image's own
# user makes room for ours at the usual first id.
RUN userdel --remove ubuntu \
 && useradd --create-home --uid 1000 --shell /bin/bash turtle \
 && mkdir /state \
 && chown turtle:turtle /state \
 && echo "source /opt/turtle/environment.sh" >> /home/turtle/.bashrc
VOLUME /state

COPY --from=build /opt/turtle/install /opt/turtle/install
COPY maps /opt/turtle/maps
# The configuration to publish in the console; the view hands it out.
COPY fleetless /opt/turtle/fleetless
COPY docker/environment.sh docker/entrypoint.sh docker/start.sh docker/launcher.py /opt/turtle/
# The ros2 daemon the launcher starts beside the nodes: the first
# `ros2 topic list` then gets the whole graph, not half of it.
COPY docker/ros2_daemon.py /opt/turtle/
# `docker exec -it turtle ros2 topic list` finds this one first.
COPY docker/ros2 /usr/local/bin/ros2

# Discovery stays inside: the nodes look for each other in this container and
# nowhere else. A peer that names this container still joins the graph, and
# SECURITY.md says who can.
ENV ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
    FLEETLESS_STATE_DIR=/state/bridge

USER turtle
WORKDIR /home/turtle
EXPOSE 8765

# The view answers 200 while the simulator's status reaches it.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --start-interval=2s --retries=3 \
    CMD curl -fsS -o /dev/null "http://127.0.0.1:${TURTLE_VIEW_PORT:-8765}/healthz" || exit 1

LABEL org.opencontainers.image.title="turtle-sim" \
      org.opencontainers.image.description="A simulated turtle in a grid world, with ROS 2 Jazzy and the Fleetless Bridge." \
      org.opencontainers.image.source="https://github.com/fleetless/turtle-sim" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      dev.fleetless.bridge.version="${BRIDGE_VERSION}" \
      dev.fleetless.bridge.deb.sha256="${BRIDGE_DEB_SHA256}"

# `docker stop` sends this, the same signal as Ctrl-C. The entrypoint hears
# it, and SIGTERM as well, from its first lines on, and has launch stop its
# nodes.
STOPSIGNAL SIGINT

ENTRYPOINT ["/opt/turtle/entrypoint.sh"]

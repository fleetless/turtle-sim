#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Everything CI runs, in Docker, so nobody needs ROS, Python packages or Node
# on the host.
#
#   ./run-tests.sh [lint|unit|ros|yaml|image|all]     (default: all)
#
#   lint    ruff, shellcheck and actionlint
#   unit    pytest on the simulation core and the cameras' renderers, in plain
#           Python without ROS
#   ros     colcon build and colcon test in ROS 2 Jazzy
#   yaml    the shipped fleetless.yaml against the contracts schema, and
#           the release script's tests
#   image   builds the image, runs its smoke test and brings
#           docker-compose.yaml up against it
#
# Names, for a machine that is shared:
#   TURTLE_TEST_IMAGE   image tag to build (default turtle-sim:test); the test
#                       images are this name with -unit and -ros appended
#   TURTLE_TEST_NAME    container name prefix (default turtle-sim-test)
#   TURTLE_TEST_PORT    host port of the smoke test (default 8765); the
#                       Compose file's test takes the one above it
#   TURTLE_TEST_WS      a Docker volume that keeps the colcon workspace
#                       between runs of `ros` (default: none, a clean build)
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=${TURTLE_TEST_IMAGE:-turtle-sim:test}
NAME=${TURTLE_TEST_NAME:-turtle-sim-test}

docker info > /dev/null 2>&1 || {
    echo "run-tests.sh: docker does not answer. Start it, or join the docker group." >&2
    exit 2
}

build_unit_image() {
    docker build -q -t "$IMAGE-unit" -f docker/unit.Dockerfile docker > /dev/null
}

run_lint() {
    build_unit_image
    docker run --rm --name "$NAME-lint" -v "$PWD:/src:ro" -w /src "$IMAGE-unit" sh -euc '
        ruff check --no-cache .
        ruff format --no-cache --check .
        # Every file with a shell shebang, and the one that is only sourced.
        { grep -rlE "^#!.*[/ ](ba)?sh( |$)" run-tests.sh docker scripts; ls docker/*.sh; } \
            | sort -u | xargs shellcheck
        if [ -d .github/workflows ]; then actionlint; fi
    '
}

run_unit() {
    build_unit_image
    # The core's own tests, the renderers', and the ones that hold the
    # repository together (the shipped map against its script). The same
    # core and renderer tests run again under `ros`, there against Ubuntu's
    # numpy and OpenCV.
    docker run --rm --name "$NAME-unit" -v "$PWD:/src:ro" -w /src \
        -e PYTHONPATH=/src/ros/fleetless_turtle_sim:/src/ros/fleetless_turtle_cameras \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "$IMAGE-unit" python -m pytest -q -p no:cacheprovider --import-mode=importlib \
        ros/fleetless_turtle_sim/test/core ros/fleetless_turtle_cameras/test/render test
}

run_ros() {
    docker build -q -t "$IMAGE-ros" -f docker/ros.Dockerfile . > /dev/null
    local workspace=()
    if [ -n "${TURTLE_TEST_WS:-}" ]; then workspace=(-v "$TURTLE_TEST_WS:/ws"); fi
    docker run --rm --name "$NAME-ros" -v "$PWD:/src:ro" "${workspace[@]}" "$IMAGE-ros" \
        /src/docker/ros-tests.sh
}

run_yaml() {
    # What runs on Node: the shipped fleetless.yaml against the schema of
    # the pinned @fleetless/contracts, and the release script's own tests,
    # which play through a git history in a temporary directory. A copy,
    # because the source is mounted read-only and pnpm installs beside it.
    docker run --rm --name "$NAME-yaml" -v "$PWD:/src:ro" node:22-bookworm bash -euc '
        mkdir /work && cd /work
        cp -r /src/package.json /src/pnpm-lock.yaml /src/scripts /src/fleetless /src/.github .
        corepack enable
        pnpm install --frozen-lockfile
        node scripts/validate-fleetless-yaml.mjs
        node --test --test-reporter=spec ".github/release/*.test.mjs"
    '
}

run_image() {
    docker build -t "$IMAGE" .
    SMOKE_NAME="$NAME-smoke" SMOKE_PORT="${TURTLE_TEST_PORT:-8765}" docker/smoke-test.sh "$IMAGE"
    # docker-compose.yaml against the same image, one port up: that test
    # moves the view away from its own port, so it needs another.
    COMPOSE_TEST_NAME="$NAME-compose" COMPOSE_TEST_PORT="$((${TURTLE_TEST_PORT:-8765} + 1))" \
        docker/compose-test.sh "$IMAGE"
}

run() {
    echo "run-tests.sh: $1" >&2
    "run_$1"
    echo "run-tests.sh: $1 passed" >&2
}

mode=${1:-all}
case "$mode" in
    lint | unit | ros | yaml | image) run "$mode" ;;
    all)
        for each in lint unit ros yaml image; do run "$each"; done
        echo "run-tests.sh: all passed (lint, unit, ros, yaml, image)" >&2
        ;;
    *)
        echo "run-tests.sh: unknown mode '$mode'. One of: lint, unit, ros, yaml, image, all." >&2
        exit 2
        ;;
esac

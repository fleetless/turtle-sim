#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Everything CI runs, in Docker, so nobody needs ROS, Python packages or Node
# on the host.
#
#   ./run-tests.sh [lint|unit|ros|yaml|image|all]     (default: all)
#
#   lint    ruff, shellcheck and actionlint
#   unit    pytest on the simulation core, in plain Python without ROS
#   ros     colcon build and colcon test in ROS 2 Jazzy
#   yaml    the shipped fleetless.yaml against the contracts schema
#   image   builds the image and runs its smoke test
#
# Names, for a machine that is shared:
#   TURTLE_TEST_IMAGE   image tag to build (default turtle-sim:test); the test
#                       images are this name with -unit and -ros appended
#   TURTLE_TEST_NAME    container name prefix (default turtle-sim-test)
#   TURTLE_TEST_PORT    host port of the smoke test (default 8765)
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
    # The core's own tests, and the ones that hold the repository together
    # (the shipped map against its script). The same core tests run again
    # under `ros`, there against Ubuntu's numpy and OpenCV.
    docker run --rm --name "$NAME-unit" -v "$PWD:/src:ro" -w /src \
        -e PYTHONPATH=/src/ros/fleetless_turtle_sim -e PYTHONDONTWRITEBYTECODE=1 \
        "$IMAGE-unit" python -m pytest -q -p no:cacheprovider --import-mode=importlib \
        ros/fleetless_turtle_sim/test/core test
}

run_ros() {
    docker build -q -t "$IMAGE-ros" -f docker/ros.Dockerfile . > /dev/null
    local workspace=()
    if [ -n "${TURTLE_TEST_WS:-}" ]; then workspace=(-v "$TURTLE_TEST_WS:/ws"); fi
    docker run --rm --name "$NAME-ros" -v "$PWD:/src:ro" "${workspace[@]}" "$IMAGE-ros" \
        /src/docker/ros-tests.sh
}

run_yaml() {
    echo "run-tests.sh: no fleetless.yaml to validate yet." >&2
    return 1
}

run_image() {
    docker build -t "$IMAGE" .
    SMOKE_NAME="$NAME-smoke" SMOKE_PORT="${TURTLE_TEST_PORT:-8765}" docker/smoke-test.sh "$IMAGE"
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

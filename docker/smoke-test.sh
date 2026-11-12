#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The image's smoke test. It needs an image in the local Docker daemon, curl,
# and one free port.
#
#   docker/smoke-test.sh <image>
#
#   SMOKE_NAME      container name (default turtle-sim-smoke); the test
#                   removes this container, and only this one
#   SMOKE_PORT      host port for the view (default 8765)
#   SMOKE_PATIENCE  factor on every timeout (default 1); an emulated
#                   architecture wants more
set -euo pipefail

image=${1:?usage: smoke-test.sh <image>}
name=${SMOKE_NAME:-turtle-sim-smoke}
port=${SMOKE_PORT:-8765}
patience=${SMOKE_PATIENCE:-1}

NO_TOKEN="FLEETLESS_TOKEN is not set: simulator and view are running, no bridge was started."
TOPICS="/cmd_vel /odom /tf /tf_static /scan /range/front /range/left /range/right
        /battery_state /turtle/status /turtle/world /map"

pass() { echo "PASS  $1"; }
fail() {
    echo "FAIL  $1" >&2
    echo "----- the container's log -----" >&2
    docker logs "$name" 2>&1 | tail -n 60 >&2 || true
    exit 1
}
remove() { docker rm --force --volumes "$name" > /dev/null 2>&1 || true; }
# Tries a command once a second until it succeeds or the time is up.
within() {
    local seconds=$(($1 * patience))
    shift
    for _ in $(seq "$seconds"); do
        if "$@" > /dev/null 2>&1; then return 0; fi
        sleep 1
    done
    return 1
}
healthz() { curl -fsS -o /dev/null "http://127.0.0.1:$port/healthz"; }
docker_says_healthy() { [ "$(docker inspect --format '{{.State.Health.Status}}' "$name")" = healthy ]; }

trap remove EXIT
remove

# --- Started without a token --------------------------------------------------
docker run --detach --name "$name" --publish "127.0.0.1:$port:8765" "$image" > /dev/null

within 30 healthz || fail "/healthz did not answer 200 within $((30 * patience)) s"
pass "/healthz answers 200"

docker logs "$name" 2>&1 | grep -qF "$NO_TOKEN" || fail "the log does not hold the no-token line"
pass "the log holds the no-token line"

within 60 docker_says_healthy || fail "Docker's own health check did not turn healthy"
pass "the image's HEALTHCHECK reports healthy"

curl -fsS "http://127.0.0.1:$port/" | grep -qF "<title>turtle-sim</title>" || fail "/ is not the page"
pass "/ is the page"

[ "$(docker exec "$name" id -u)" != 0 ] || fail "the container runs as root"
pass "the container does not run as root"

# --- The ROS graph, without sourcing anything by hand -------------------------
graph_is_complete() {
    local listed topic
    listed=$(docker exec "$name" ros2 topic list) || return 1
    for topic in $TOPICS; do
        grep -qxF "$topic" <<< "$listed" || return 1
    done
}
within 30 graph_is_complete || fail "\`ros2 topic list\` inside does not name every topic"
pass "\`ros2 topic list\` inside names the topics"

# --- The bridge alone ---------------------------------------------------------
# Without a token it prints its own sentence and exits 2, which shows that
# the package and its dependencies load.
set +e
said=$(docker run --rm --name "$name-bridge" "$image" ros2 run fleetless_bridge bridge 2>&1)
code=$?
set -e
[ "$code" -eq 2 ] || fail "the bridge alone exited $code, not 2: $said"
grep -qF "FLEETLESS_TOKEN is not set." <<< "$said" || fail "the bridge did not print its sentence: $said"
pass "the bridge alone prints its sentence and exits 2"

# --- docker stop --------------------------------------------------------------
started=$(date +%s)
docker stop "$name" > /dev/null
took=$(($(date +%s) - started))
code=$(docker inspect --format '{{.State.ExitCode}}' "$name")
# 137 would be Docker's SIGKILL after the grace period.
if [ "$took" -ge 10 ] || [ "$code" -ne 0 ]; then
    fail "docker stop took $took s and the container exited $code"
fi
pass "docker stop ends the container in $took s, exit 0"
remove

# --- A simulator that ends takes the container with it ------------------------
set +e
said=$(docker run --rm --name "$name" --env TURTLE_MAP=/nowhere/garden.png "$image" 2>&1)
code=$?
set -e
[ "$code" -ne 0 ] || fail "a map that does not exist left the container at exit 0"
grep -qF "Map /nowhere/garden.png does not exist." <<< "$said" || fail "the refusal lacks its sentence: $said"
if grep -qF "Traceback" <<< "$said"; then fail "the refusal shows a stack trace: $said"; fi
pass "a map that does not exist ends the container with one sentence, exit $code"

echo "smoke-test.sh: $image passed"

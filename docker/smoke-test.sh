#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The image's smoke test. It needs an image in the local Docker daemon, curl,
# and one free port.
#
#   docker/smoke-test.sh <image>
#
#   SMOKE_NAME      container name (default turtle-sim-smoke); the test
#                   removes this container and the two it names after it,
#                   with -second and -bridge appended, and no other
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
        /battery_state /turtle/status /turtle/world /map
        /turtle/camera/top/image_raw /turtle/camera/eye/image_raw"

# What is searched is fetched whole first and searched after. A pipe into
# `grep -q` ends when grep has seen enough, and under pipefail the writer it
# cut off fails the line: a check that fails on a full match, or passes on
# one it should have refused.

# A container that had to end by itself is removed once it has. What it said
# is in $said, and that is the log to show until its claim has passed.
ended=
said=
pass() {
    echo "PASS  $1"
    ended=
}
fail() {
    echo "FAIL  $1" >&2
    echo "----- the container's log -----" >&2
    if [ -n "$ended" ]; then
        tail -n 60 <<< "$said" >&2
    else
        docker logs "$name" 2>&1 | tail -n 60 >&2 || true
    fi
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
# Starts a container that has to end by itself and waits for it: what it
# said is in $said afterwards, its exit code in $code. One that is still
# running after a minute does not hold the test up. It is removed, and the
# claim fails with a sentence.
#   to_its_end <what is being claimed> <container name> <docker run arguments>
to_its_end() {
    local claim=$1 container=$2 limit=$((60 * patience))
    shift 2
    ended=$container
    docker rm --force --volumes "$container" > /dev/null 2>&1 || true
    docker run --detach --name "$container" "$@" > /dev/null
    if ! code=$(timeout "$limit" docker wait "$container"); then
        said=$(docker logs "$container" 2>&1 | tail -n 20)
        docker rm --force --volumes "$container" > /dev/null 2>&1 || true
        fail "$claim: the container did not end within $limit s. Its last lines: $said"
    fi
    said=$(docker logs "$container" 2>&1)
    docker rm --force --volumes "$container" > /dev/null 2>&1 || true
}
healthz() { curl -fsS -o /dev/null "http://127.0.0.1:$port/healthz"; }
docker_says_healthy() { [ "$(docker inspect --format '{{.State.Health.Status}}' "$name")" = healthy ]; }

trap remove EXIT
remove

# --- Started without a token --------------------------------------------------
docker run --detach --name "$name" --publish "127.0.0.1:$port:8765" "$image" > /dev/null

within 30 healthz || fail "/healthz did not answer 200 within $((30 * patience)) s"
pass "/healthz answers 200"

log=$(docker logs "$name" 2>&1)
grep -qF "$NO_TOKEN" <<< "$log" || fail "the log does not hold the no-token line"
pass "the log holds the no-token line"

within 60 docker_says_healthy || fail "Docker's own health check did not turn healthy"
pass "the image's HEALTHCHECK reports healthy"

page=$(curl -fsS "http://127.0.0.1:$port/")
grep -qF "<title>turtle-sim</title>" <<< "$page" || fail "/ is not the page"
pass "/ is the page"

config=$(curl -fsS "http://127.0.0.1:$port/fleetless.yaml")
grep -qx "fleetless: 1" <<< "$config" || fail "/fleetless.yaml is not the shipped configuration"
pass "/fleetless.yaml is the shipped configuration"

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

# --- Both cameras, on the shared memory Docker gives a container ---------------
# 64 MB of /dev/shm, and ROS moves messages between processes through it. A
# `top` picture is 691 kB, ten times a second. A subscriber like the bridge's,
# a process of its own inside the container, counts both for five seconds.
[ "$(docker inspect --format '{{.HostConfig.ShmSize}}' "$name")" = 67108864 ] \
    || fail "the container was not started with Docker's default /dev/shm of 64 MB"
pictures=$(docker exec -i "$name" /opt/turtle/entrypoint.sh python3 - << 'PYTHON'
import os
import time

import rclpy
from sensor_msgs.msg import Image

rclpy.init()
node = rclpy.create_node("smoke_test")
heard = {"top": [], "eye": []}
for camera, pictures in heard.items():
    node.create_subscription(
        Image,
        f"/turtle/camera/{camera}/image_raw",
        lambda image, pictures=pictures: pictures.append((time.monotonic(), image)),
        10,
    )
# Until both have been heard once, then five seconds more.
deadline = time.monotonic() + 30.0
while not all(heard.values()) and time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
since = time.monotonic()
while time.monotonic() < since + 5.0:
    rclpy.spin_once(node, timeout_sec=0.1)
for camera, pictures in heard.items():
    count = sum(at >= since for at, _ in pictures)
    image = pictures[-1][1] if pictures else Image()
    print(camera, f"{image.width}x{image.height}", image.encoding or "-", len(image.data), count)
in_use = sum(os.path.getsize(os.path.join("/dev/shm", name)) for name in os.listdir("/dev/shm"))
print("shm", in_use // 2**20)
PYTHON
) || fail "the subscriber inside the container did not run: $pictures"
# Ten a second for five seconds; a busy or an emulated machine may lose some.
fewest=$((35 / patience))
for camera in "top 480x480 691200" "eye 320x240 230400"; do
    read -r slug size bytes <<< "$camera"
    read -r _ got_size encoding got_bytes count <<< "$(grep "^$slug " <<< "$pictures")"
    [ "$got_size $encoding $got_bytes" = "$size rgb8 $bytes" ] \
        || fail "$slug is $got_size $encoding with $got_bytes bytes, not $size rgb8 with $bytes"
    if [ "$count" -lt "$fewest" ] || [ "$count" -gt 55 ]; then
        fail "$slug delivered $count pictures in 5 s, not about 50"
    fi
    pass "$slug flows: $size rgb8, $count pictures in 5 s"
done
read -r _ shm_mb <<< "$(grep "^shm " <<< "$pictures")"
pass "both on Docker's default /dev/shm: $shm_mb MB of 64 in use"

# --- A second turtle on the same port -----------------------------------------
# It shares the first one's network, so its view finds the port taken. The
# view says so and ends, and the simulator beside it is told to go: one
# sentence, no stack trace from either.
to_its_end "a view port in use" "$name-second" --network "container:$name" \
    --env ROS_DOMAIN_ID=77 "$image"
[ "$code" -ne 0 ] || fail "a view port in use left the container at exit 0"
grep -qF "Port 8765 is in use: the view cannot listen there." <<< "$said" \
    || fail "the refusal lacks its sentence: $said"
if grep -qF "Traceback" <<< "$said"; then fail "the refusal shows a stack trace: $said"; fi
pass "a view port in use ends the container with one sentence, exit $code"

# --- The bridge alone ---------------------------------------------------------
# Without a token it prints its own sentence and exits 2, which shows that
# the package and its dependencies load.
to_its_end "the bridge alone" "$name-bridge" "$image" ros2 run fleetless_bridge bridge
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

# --- A stop at any moment -------------------------------------------------------
# Early ones above all: the first process is a shell for a moment, then
# Python imports ROS for a second, then the nodes do the same. At each delay
# the container has to end within five seconds, by itself and not by
# Docker's SIGKILL (exit 137), and without a stack trace in its log.
for delay in 0.1 0.2 0.3 0.5 0.8 1.0 1.2 1.5 2 3; do
    docker run --detach --name "$name" "$image" > /dev/null
    sleep "$delay"
    asked=$(date +%s%N)
    docker stop "$name" > /dev/null
    took_ms=$((($(date +%s%N) - asked) / 1000000))
    code=$(docker inspect --format '{{.State.ExitCode}}' "$name")
    if [ "$took_ms" -ge $((5000 * patience)) ] || [ "$code" -ne 0 ]; then
        fail "docker stop $delay s after the start took $took_ms ms and the container exited $code"
    fi
    log=$(docker logs "$name" 2>&1)
    if grep -qF "Traceback" <<< "$log"; then
        fail "docker stop $delay s after the start left a stack trace"
    fi
    # Nor does the entrypoint's shell report how its job ended. A stop that
    # was asked for is no news, and "User defined signal 1" explains nothing.
    if grep -qE "User defined signal|entrypoint\.sh: line" <<< "$log"; then
        fail "docker stop $delay s after the start left the shell's job notice"
    fi
    pass "docker stop $delay s after the start: ended in $took_ms ms, exit $code, a clean log"
    remove
done

# --- The same stop, with the entrypoint held back --------------------------------
# The delays above meet the job notice only now and then: bash prints it
# when the turtle ends between two of the entrypoint's lines instead of
# inside its `wait`, which takes a busy machine and luck. Here every line of
# the container's shells takes 50 ms (a DEBUG trap, handed in through
# BASH_ENV), so the turtle is still setting up its environment when the stop
# comes, ends on the spot, and is long gone before the entrypoint reaches
# its next line. That is the moment, every time.
slow=$(mktemp)
printf 'set -T\ntrap "sleep 0.05" DEBUG\n' > "$slow"
chmod 644 "$slow"
docker create --name "$name" --env BASH_ENV=/tmp/slow.sh "$image" > /dev/null
# `docker cp` says "Successfully copied" and can be told not to only since
# Docker 25. So what it says is kept, and shown if it failed.
copied=$(docker cp "$slow" "$name:/tmp/slow.sh" 2>&1) || {
    rm -f "$slow"
    fail "the DEBUG trap did not reach the container: $copied"
}
rm -f "$slow"
docker start "$name" > /dev/null
# Long enough for the trap to be set, on a slow machine too. The turtle
# needs seven times as long to come up under it.
sleep $((2 * patience))
asked=$(date +%s%N)
docker stop "$name" > /dev/null
took_ms=$((($(date +%s%N) - asked) / 1000000))
code=$(docker inspect --format '{{.State.ExitCode}}' "$name")
if [ "$took_ms" -ge $((5000 * patience)) ] || [ "$code" -ne 0 ]; then
    fail "docker stop with the entrypoint held back took $took_ms ms and the container exited $code"
fi
log=$(docker logs "$name" 2>&1)
if grep -qE "User defined signal|entrypoint\.sh: line" <<< "$log"; then
    fail "docker stop with the entrypoint held back left the shell's job notice"
fi
# A log with anything in it means the turtle was up already, and the stop
# met another moment than the one this claim is about.
if [ -n "$log" ]; then
    fail "the entrypoint held back was not slow enough: the turtle had started before the stop came"
fi
pass "docker stop with the entrypoint held back: ended in $took_ms ms, exit $code, an empty log"
remove

# --- A turtle ended by a signal nobody sent -------------------------------------
# The kernel does that when memory runs out. The entrypoint is silent about
# a stop that was asked for and about nothing else, so here bash's notice of
# its job is in the log and names the signal. The exit code stays the
# shell's 128 plus the signal.
docker run --detach --name "$name" "$image" > /dev/null
turtle_runs() {
    log=$(docker logs "$name" 2>&1) || return 1
    grep -qF "$NO_TOKEN" <<< "$log"
}
within 30 turtle_runs || fail "the turtle did not start"
# The turtle is the process that runs launcher.py. /proc knows it without
# any tool the image may not have.
docker exec "$name" bash -c '
    for process in /proc/[0-9]*; do
        mapfile -d "" -t argv < "$process/cmdline" 2> /dev/null || continue
        if [ "${argv[1]:-}" = /opt/turtle/launcher.py ]; then
            kill -KILL "${process#/proc/}"
        fi
    done'
code=$(timeout $((60 * patience)) docker wait "$name") \
    || fail "the container did not end with its turtle"
log=$(docker logs "$name" 2>&1)
[ "$code" -eq 137 ] || fail "a turtle ended by SIGKILL left the container at exit $code, not 137"
grep -qE "entrypoint\.sh: line [0-9]+: +[0-9]+ Killed" <<< "$log" \
    || fail "a turtle ended by SIGKILL is not named in the log"
pass "a turtle ended by SIGKILL: the log says Killed, exit $code"
remove

# --- A container that may not start a process -----------------------------------
# With one process allowed, the entrypoint cannot start the turtle. bash
# tries for a quarter of a minute and gives up. What it says about that is
# the only word there is, and it has to reach the log.
to_its_end "a container that may not start a process" "$name" --pids-limit 1 "$image"
[ "$code" -ne 0 ] || fail "a container that may not start a process left exit 0"
grep -qF "fork" <<< "$said" || fail "a fork that failed is not named in the log: $said"
pass "a container that may not start a process says so, exit $code"

# --- An empty FLEETLESS_CLOUD_URL is an unset one -------------------------------
# docker-compose.yaml passes the variable empty when nobody set it. The
# bridge says at its start which cloud it will connect to: its own default
# then, and the one it was given otherwise. The token is none and the
# container has no network, so it reaches nobody while it says so.
bridge_names_its_cloud() {
    log=$(docker logs "$name" 2>&1) || return 1
    grep -qE "Starting fleetless_bridge .*, cloud " <<< "$log"
}
for given in "" ws://cloud.invalid/bridge; do
    docker run --detach --name "$name" --network none --env FLEETLESS_TOKEN=frt_not_a_token \
        --env "FLEETLESS_CLOUD_URL=$given" "$image" > /dev/null
    within 30 bridge_names_its_cloud || fail "with a token, the bridge did not say which cloud it connects to"
    if [ -n "$given" ]; then
        grep -qE ", cloud $given\$" <<< "$log" || fail "FLEETLESS_CLOUD_URL=$given did not reach the bridge"
        pass "FLEETLESS_CLOUD_URL=$given reaches the bridge"
    else
        grep -qE ", cloud wss?://[^ ]+\$" <<< "$log" || fail "an empty FLEETLESS_CLOUD_URL reached the bridge as its cloud"
        pass "an empty FLEETLESS_CLOUD_URL leaves the bridge its own default"
    fi
    remove
done

# --- A view port that is no port ----------------------------------------------
to_its_end "a view port that is no port" "$name" --env TURTLE_VIEW_PORT=abc "$image"
[ "$code" -ne 0 ] || fail "a view port that is no port left the container at exit 0"
grep -qF 'TURTLE_VIEW_PORT (the parameter port) is "abc": the view listens on a whole number from 1 to 65535.' <<< "$said" \
    || fail "the refusal lacks its sentence: $said"
if grep -qF "Traceback" <<< "$said"; then fail "the refusal shows a stack trace: $said"; fi
pass "a view port that is no port ends the container with one sentence, exit $code"

# --- A simulator that ends takes the container with it ------------------------
to_its_end "a map that does not exist" "$name" --env TURTLE_MAP=/nowhere/garden.png "$image"
[ "$code" -ne 0 ] || fail "a map that does not exist left the container at exit 0"
grep -qF "Map /nowhere/garden.png does not exist." <<< "$said" || fail "the refusal lacks its sentence: $said"
if grep -qF "Traceback" <<< "$said"; then fail "the refusal shows a stack trace: $said"; fi
pass "a map that does not exist ends the container with one sentence, exit $code"

# --- The test's own failure report ----------------------------------------------
# A claim that fails shows what its container said, also when that container
# had to end by itself and is gone by then.
to_its_end "the failure report" "$name" "$image" echo "a line of the container's own"
report=$( (fail "on purpose") 2>&1) || true
grep -qF "a line of the container's own" <<< "$report" \
    || fail "a failed claim does not show what its container said, but: $report"
pass "a failed claim shows what its container said"

echo "smoke-test.sh: $image passed"

#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The Compose file's test: docker-compose.yaml starts the same turtle as the
# one `docker run` line. It needs an image in the local Docker daemon, Docker
# Compose v2, curl, and one free port.
#
#   docker/compose-test.sh <image>
#
#   COMPOSE_TEST_NAME      the container's and the Compose project's name
#                          (default turtle-sim-compose); the test removes
#                          this container, the volume and the network Compose
#                          names after the project, and nothing else
#   COMPOSE_TEST_PORT      host port for the view (default 8766); any but
#                          8765, which is the turtle's own and what the test
#                          has to move away from
#   COMPOSE_TEST_PATIENCE  factor on every timeout (default 1)
#
# The shipped file names the released image, a container called `turtle` and
# the view's own port. None of them is the test's to take: the image under
# test, the container's name and the host port come from an override file
# written here, never from a variable in the shipped file. The one port the
# shipped file itself publishes in this test is TURTLE_VIEW_PORT, the
# variable that is there for it.
set -euo pipefail
cd "$(dirname "$0")/.."

image=${1:?usage: compose-test.sh <image>}
name=${COMPOSE_TEST_NAME:-turtle-sim-compose}
port=${COMPOSE_TEST_PORT:-8766}
patience=${COMPOSE_TEST_PATIENCE:-1}

if [ "$port" = 8765 ]; then
    echo "compose-test.sh: COMPOSE_TEST_PORT is 8765, the turtle's own port. Name another." >&2
    exit 2
fi

NO_TOKEN="FLEETLESS_TOKEN is not set: simulator and view are running, no bridge was started."
VARIABLES="FLEETLESS_TOKEN FLEETLESS_CLOUD_URL TURTLE_MAP TURTLE_BATTERY_DRAIN TURTLE_VIEW_PORT"

# A copy of the shipped file in a directory of its own. Compose reads `.env`
# beside the file it is given, and a checkout may hold a real one, with a
# real token in it.
work=$(mktemp -d)
cp docker-compose.yaml "$work/"

pass() { echo "PASS  $1"; }
fail() {
    echo "FAIL  $1" >&2
    # The container's log, where the claim had a container.
    if log=$(docker logs "$name" 2>&1); then
        echo "----- the container's log -----" >&2
        tail -n 60 <<< "$log" >&2
    fi
    exit 1
}
clean_up() {
    docker rm --force --volumes "$name" > /dev/null 2>&1 || true
    docker volume rm "${name}_turtle-state" > /dev/null 2>&1 || true
    docker network rm "${name}_default" > /dev/null 2>&1 || true
    rm -rf "$work"
}
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
# Compose on the copy, and on the override file once there is one. Of the
# turtle's variables it sees those in $given and no other: whatever this
# shell holds is not the test's.
given=()
override=
compose() {
    local variable unset=() files=(--file "$work/docker-compose.yaml")
    for variable in $VARIABLES; do unset+=(-u "$variable"); done
    if [ -n "$override" ]; then files+=(--file "$override"); fi
    env "${unset[@]}" "${given[@]}" docker compose --project-name "$name" \
        --project-directory "$work" "${files[@]}" "$@"
}
# What `docker compose config` printed holds a line `key: value`, whatever
# quotes this Compose puts around the value.
holds() { grep -qE "^ *$1: [\"']?$2[\"']?\$" <<< "$rendered"; }
healthz() { curl -fsS -o /dev/null "http://127.0.0.1:$port/healthz"; }
gone() { [ -z "$(docker ps --all --quiet --filter "name=^$name\$")" ]; }
# The override file: the image under test and the test's own container name,
# and whatever lines are given beside them.
override_with() {
    override=$work/override.yaml
    {
        echo "services:"
        echo "  turtle:"
        echo "    image: $image"
        echo "    container_name: $name"
        printf '%s\n' "$@"
    } > "$override"
}

trap clean_up EXIT
docker rm --force --volumes "$name" > /dev/null 2>&1 || true

# --- docker compose config, with no variable set --------------------------------
rendered=$(compose config 2> "$work/config.err") || fail "Compose refuses the file: $(cat "$work/config.err")"
# A variable without a default would have Compose say so.
if grep -qiF "is not set" "$work/config.err"; then
    fail "Compose misses a default: $(cat "$work/config.err")"
fi
holds image "ghcr.io/fleetless/turtle-sim:latest" || fail "the file does not name the released image"
holds container_name turtle || fail "the container is not called turtle"
holds FLEETLESS_TOKEN "" || fail "FLEETLESS_TOKEN is not passed empty"
holds FLEETLESS_CLOUD_URL "" || fail "FLEETLESS_CLOUD_URL is not passed empty"
holds TURTLE_MAP /opt/turtle/maps/garden.png || fail "TURTLE_MAP lacks its default"
holds TURTLE_BATTERY_DRAIN 1.0 || fail "TURTLE_BATTERY_DRAIN lacks its default"
holds TURTLE_VIEW_PORT 8765 || fail "TURTLE_VIEW_PORT lacks its default"
holds host_ip 127.0.0.1 || fail "the view is not published on 127.0.0.1 only"
holds target 8765 || fail "the view's port in the container is not 8765"
holds published 8765 || fail "the view's port on the host is not 8765"
pass "docker compose config accepts the file with no variable set, every default in place"

# --- docker compose config, with every variable set -----------------------------
given=(FLEETLESS_TOKEN=frt_not_a_token FLEETLESS_CLOUD_URL=wss://cloud.invalid/bridge
    TURTLE_MAP=/maps/my-map.png TURTLE_BATTERY_DRAIN=2.5 TURTLE_VIEW_PORT=8123)
rendered=$(compose config 2> "$work/config.err") \
    || fail "Compose refuses the file with every variable set: $(cat "$work/config.err")"
given=()
holds FLEETLESS_TOKEN frt_not_a_token || fail "FLEETLESS_TOKEN does not arrive"
holds FLEETLESS_CLOUD_URL wss://cloud.invalid/bridge || fail "FLEETLESS_CLOUD_URL does not arrive"
holds TURTLE_MAP /maps/my-map.png || fail "TURTLE_MAP does not arrive"
holds TURTLE_BATTERY_DRAIN 2.5 || fail "TURTLE_BATTERY_DRAIN does not arrive"
holds TURTLE_VIEW_PORT 8123 || fail "TURTLE_VIEW_PORT does not arrive"
holds target 8123 || fail "the port in the container does not follow TURTLE_VIEW_PORT"
holds published 8123 || fail "the port on the host does not follow TURTLE_VIEW_PORT"
pass "docker compose config accepts the file with every variable set, every value in place"

# --- docker compose up, with no variable set ------------------------------------
# The view stays on 8765 in the container. On the host that port may be
# somebody's turtle, so the override publishes it on the test's port instead.
override_with "    ports: !override" "      - \"127.0.0.1:$port:8765\""
compose up --detach > "$work/up.log" 2>&1 || fail "docker compose up failed: $(cat "$work/up.log")"

within 30 healthz || fail "/healthz did not answer 200 within $((30 * patience)) s"
pass "docker compose up: /healthz answers 200"

log=$(docker logs "$name" 2>&1)
grep -qF "$NO_TOKEN" <<< "$log" || fail "the log does not hold the no-token line"
pass "the log holds the no-token line"

[ "$(docker port "$name")" = "8765/tcp -> 127.0.0.1:$port" ] \
    || fail "the view is published as $(docker port "$name")"
pass "the view is published on 127.0.0.1 and nowhere else"

# What `docker compose up` followed by Ctrl-C does: the container ends by
# itself, well inside Docker's ten seconds.
asked=$(date +%s%N)
compose stop > /dev/null 2>&1
took_ms=$((($(date +%s%N) - asked) / 1000000))
code=$(docker inspect --format '{{.State.ExitCode}}' "$name")
if [ "$took_ms" -ge $((5000 * patience)) ] || [ "$code" -ne 0 ]; then
    fail "docker compose stop took $took_ms ms and the container exited $code"
fi
pass "docker compose stop ends the container in $took_ms ms, exit $code"

compose down > /dev/null 2>&1 || fail "docker compose down failed"
gone || fail "docker compose down left the container"
pass "docker compose down leaves no container"

# --- docker compose up, with TURTLE_VIEW_PORT in .env ---------------------------
# `.env.example` is the template: copied, with the one line set. The view
# then listens on that port, and the shipped file publishes it there, on
# both sides, with no help from the override.
sed "s/^# TURTLE_VIEW_PORT=.*/TURTLE_VIEW_PORT=$port/" .env.example > "$work/.env"
grep -qxF "TURTLE_VIEW_PORT=$port" "$work/.env" || fail ".env.example has no line for TURTLE_VIEW_PORT"
override_with
compose up --detach > "$work/up.log" 2>&1 || fail "docker compose up failed: $(cat "$work/up.log")"

within 30 healthz || fail "with TURTLE_VIEW_PORT=$port, /healthz did not answer 200 on that port"
[ "$(docker port "$name")" = "$port/tcp -> 127.0.0.1:$port" ] \
    || fail "with TURTLE_VIEW_PORT=$port, the view is published as $(docker port "$name")"
pass "with TURTLE_VIEW_PORT=$port in .env, the view answers there"

log=$(docker logs "$name" 2>&1)
grep -qF "$NO_TOKEN" <<< "$log" || fail "with .env.example as .env, the log does not hold the no-token line"
pass "with .env.example as .env, no bridge is started"

compose down > /dev/null 2>&1 || fail "docker compose down failed"
gone || fail "docker compose down left the container"
pass "docker compose down leaves no container"

echo "compose-test.sh: docker-compose.yaml passed with $image"

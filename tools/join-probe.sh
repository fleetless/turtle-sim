#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Runs tools/join_probe.py in fresh containers of an image: twice on two
# CPUs and twice without a limit, one container after the other. The rate
# at which the world stands differs a lot from container to container, so
# one quiet run says nothing.
#
#   tools/join-probe.sh <image> [action|service|nobody] [joins]
#
#   JOIN_PROBE_NAME   container name (default turtle-join-probe); the script
#                     removes this container and no other
#   JOIN_PROBE_CPUS   the limits to measure under (default "2 none")
#   JOIN_PROBE_RUNS   fresh containers per limit (default 2)
#   JOIN_PROBE_ARGS   more arguments for `docker run`, split at blanks
#
# It prints the probe's lines for every container and one line per container
# at the end. A probe that did not finish is named in both, and the script
# goes on to the next container. Exit code 0 means every container was
# measured.
set -euo pipefail

image=${1:?usage: join-probe.sh <image> [action|service|nobody] [joins]}
kind=${2:-action}
joins=${3:-30}
name=${JOIN_PROBE_NAME:-turtle-join-probe}
read -r -a limits <<< "${JOIN_PROBE_CPUS:-2 none}"
read -r -a more <<< "${JOIN_PROBE_ARGS:-}"
runs=${JOIN_PROBE_RUNS:-2}
probe="$(dirname "$0")/join_probe.py"

remove() { docker rm --force --volumes "$name" > /dev/null 2>&1 || true; }
trap remove EXIT

summary=()
failed=0
for limit in "${limits[@]}"; do
    cpus=()
    if [ "$limit" != none ]; then cpus=(--cpus "$limit"); fi
    for run in $(seq "$runs"); do
        remove
        docker run --detach --name "$name" "${cpus[@]}" "${more[@]}" "$image" > /dev/null
        # Up: the view answers once the simulator's status reaches it. Then
        # ten seconds more, until the nodes have found each other.
        for _ in $(seq 60); do
            if docker exec "$name" curl -fsS -o /dev/null http://127.0.0.1:8765/healthz 2> /dev/null; then
                break
            fi
            sleep 1
        done
        sleep 10
        docker cp "$probe" "$name:/tmp/join_probe.py" > /dev/null
        echo "--- cpus $limit, container $run of $runs, load $(cut -d ' ' -f 1 /proc/loadavg)"
        code=0
        said=$(docker exec "$name" /opt/turtle/entrypoint.sh python3 -u /tmp/join_probe.py "$kind" "$joins") || code=$?
        echo "$said"
        line=$(tail -n 1 <<< "$said")
        if [ "$code" -ne 0 ]; then
            failed=1
            line="${line:-nothing measured}; the probe ended with exit code $code"
            echo "the probe ended with exit code $code"
        fi
        summary+=("cpus $limit, container $run: $line")
        remove
    done
done
echo "--- $image, $kind, $joins joins in each container"
printf '%s\n' "${summary[@]}"
exit "$failed"

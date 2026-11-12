#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The container's first process.
#
# Without arguments it starts the turtle beside itself and waits for it: the
# launch file with the simulator, the view and, with a token, the bridge.
# With arguments it runs that command in the turtle's ROS environment.
#
# It stays the first process for one reason: a stop. `docker stop` and
# Ctrl-C send a signal to the first process and to nobody else, and a first
# process only gets the signals it has asked for. Python needs a second or
# two before it has asked: it is importing ROS. Handed the place too early,
# it either never hears the stop, and Docker kills the container ten seconds
# later, or it answers with a stack trace. So this shell keeps the place,
# hears SIGINT and SIGTERM from the lines below on, and passes a stop on in
# a form that is safe at every moment.
set -eo pipefail

if [ "$#" -gt 0 ]; then
    exec /opt/turtle/start.sh "$@"
fi

stopping=
turtle=
# shellcheck disable=SC2317  # reached through the trap below
stop() {
    stopping=yes
    # SIGUSR1 means "stop" to the turtle once it listens (launcher.py). Before
    # that nothing catches it, and it ends what is there on the spot, before
    # anything was started. Either way no stop is lost.
    [ -z "$turtle" ] || kill -USR1 "$turtle" 2> /dev/null || true
}
trap stop INT TERM

# In the background, so this shell can wait and listen at once. A script's
# background job is born with SIGINT ignored, and so is all it starts until
# a process asks for the signal itself: no Python in there meets an
# interrupt with its default, the stack trace. In a session of its own, so
# that Ctrl-C in a terminal reaches this shell alone and takes the same
# path as `docker stop`.
setsid /opt/turtle/start.sh &
turtle=$!
# A stop that came before the line above found nobody to tell.
[ -z "$stopping" ] || kill -USR1 "$turtle" 2> /dev/null || true

# `wait` returns as soon as a stop arrives, with the turtle still on its way
# down. So: wait until it is gone, then ask how it ended. Whichever `wait`
# finds the turtle ended by a signal would have bash announce it ("User
# defined signal 1"); that is this script's own doing and nobody's news, so
# the announcement goes nowhere. The status stays.
while kill -0 "$turtle" 2> /dev/null; do
    wait "$turtle" 2> /dev/null || true
done
code=0
wait "$turtle" 2> /dev/null || code=$?

# 138 is "ended by SIGUSR1": the stop came before the turtle listened.
# Stopped on request is stopped, whenever the request came.
if [ -n "$stopping" ] && [ "$code" -eq 138 ]; then
    code=0
fi
exit "$code"

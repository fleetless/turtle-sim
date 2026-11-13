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

# First of all, an ear for a stop. Until the trap below is set, a stop is
# lost, and Docker kills the container ten seconds later. So nothing that
# can wait stands before it, only what `stop` needs to find when it runs:
# the two things it writes and reads, and the container's stderr under a
# second number. That one is for the turtle, which gets it whatever has
# become of this shell's own by then.
exec 3>&2
stopping=
turtle=
# shellcheck disable=SC2317  # reached through the trap below
stop() {
    # bash announces a job that a signal ended ("User defined signal 1"), on
    # its own stderr, at the first moment it notices: inside `wait`, or
    # between two lines when the job ended there. The signal below ends the
    # turtle that way when it comes before the turtle listens. That is this
    # script's own doing and nobody's news, so from here on this shell's
    # stderr goes nowhere. From here on and not before: without a stop, what
    # bash has to say is the news. A turtle the kernel killed for its memory
    # ("Killed"), a segmentation fault, a fork that failed.
    exec 2> /dev/null
    stopping=yes
    # SIGUSR1 means "stop" to the turtle once it listens (launcher.py). Before
    # that nothing catches it, and it ends what is there on the spot, before
    # anything was started. Either way no stop is lost.
    [ -z "$turtle" ] || kill -USR1 "$turtle" || true
}
trap stop INT TERM

# An empty FLEETLESS_CLOUD_URL is an unset one. docker-compose.yaml names
# every variable, and one that nobody set arrives here empty. The bridge
# would take the empty text for the cloud's address; without the variable it
# uses its own default.
if [ -z "${FLEETLESS_CLOUD_URL:-}" ]; then
    unset FLEETLESS_CLOUD_URL
fi

# A FLEETLESS_TOKEN that is empty or only blanks is an unset one too. The
# launch file takes any text for a token and starts the bridge; the bridge
# strips it, finds nothing left, refuses with exit code 2, and is started
# again five seconds later, for as long as the container runs. Without the
# variable the two agree by themselves: no token, no bridge. Blanks around a
# real token stay: the bridge strips them.
if [ -z "${FLEETLESS_TOKEN//[[:space:]]/}" ]; then
    unset FLEETLESS_TOKEN
fi

if [ "$#" -gt 0 ]; then
    # A command in the turtle's place. start.sh sets up its environment and
    # hands it the place. It is read into this shell and not started beside
    # it: a new process would have to ask for the signals again, and until
    # it had, a stop would be lost a second time. A stop before the command
    # has taken over is answered with 130, here as in start.sh, and one that
    # came before this line is not forgotten. The descriptor was the
    # turtle's.
    trap 'exit 130' INT TERM
    [ -z "$stopping" ] || exit 130
    exec 3>&-
    # shellcheck source=/dev/null
    source /opt/turtle/start.sh "$@"
fi

# In the background, so this shell can wait and listen at once. A script's
# background job is born with SIGINT ignored, and so is all it starts until
# a process asks for the signal itself: no Python in there meets an
# interrupt with its default, the stack trace. In a session of its own, so
# that Ctrl-C in a terminal reaches this shell alone and takes the same
# path as `docker stop`. With the container's stderr for its own, also when
# a stop came first and this shell's is gone.
setsid /opt/turtle/start.sh 2>&3 3>&- &
turtle=$!
# A stop that came before the line above found nobody to tell.
[ -z "$stopping" ] || kill -USR1 "$turtle" || true

# `wait` returns as soon as a stop arrives, with the turtle still on its way
# down. So: wait until it is gone, then ask how it ended. That the turtle is
# gone is what `kill -0` is asked for, and its "No such process" no error.
while kill -0 "$turtle" 2> /dev/null; do
    wait "$turtle" || true
done
code=0
wait "$turtle" || code=$?

# 138 is "ended by SIGUSR1": the stop came before the turtle listened.
# Stopped on request is stopped, whenever the request came.
if [ -n "$stopping" ] && [ "$code" -eq 138 ]; then
    code=0
fi
exit "$code"

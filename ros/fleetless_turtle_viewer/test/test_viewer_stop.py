# SPDX-License-Identifier: Apache-2.0
"""A stop has a bound, also when ROS does not let go.

What ends the node is ROS's to do, once the HTTP server has closed: it
destroys the node and shuts the context down, and neither call takes a
bound. Here one of them never returns. A stand-in sees to that: a small
library, built by this test and loaded in front of ROS's own, that passes
both calls on unless it is told to keep one. The node is the installed
one, unchanged.

Three seconds after it was asked to stop, the node says one sentence and
ends with exit code 0. That needs Python, and a ROS call that hangs keeps
Python's lock: then the node cannot say anything, and a second later
Python's faulthandler writes where every thread stands and ends the process
with exit code 1. A stop that meets no such call says nothing.

Whoever hears the stop arms the faulthandler, at once. A thread that has to
be woken for it comes too late on a busy machine, when the stop has reached
the call that keeps the lock. A second stand-in, a few lines of Python
loaded before the node's own, makes that thread late on purpose.
"""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest
from ament_index_python.packages import get_package_prefix

# A port of this file's own: the node's other tests run views on 18765 and above.
PORT = 18790
VIEWER = Path(get_package_prefix("fleetless_turtle_viewer")) / "lib/fleetless_turtle_viewer/viewer"
COMMAND = [str(VIEWER), "--ros-args", "-p", f"port:={PORT}"]
STARTED = f"The view listens on port {PORT}."
GAVE_UP = "The view did not finish stopping within 3 s and ends without it."
LAST_RESORT = "Timeout (0:00:04)!"
ASKED_AT_START = "stand-in: asked to stop while ROS starts"
ASKED_AGAIN = "stand-in: asked to stop again, 1.5 s later"
WILL_BE_LATE = "stand-in: the thread that waits for the stop will be late"

# The two calls of the stop that can hang, and where each is made.
CALLS = [("rcl_node_fini", "in destroy_node"), ("rcl_shutdown", "in try_shutdown")]

# In front of ROS's C library. It is told through the environment:
#   STAND_IN_NEVER_RETURNS    the call that does not return
#   STAND_IN_LETS_PYTHON_RUN  it gives up Python's lock first, as rclpy does
#                             around a call it expects to wait in. Around
#                             these two it does not.
#   STAND_IN_STOPS_AT_START   the stop arrives while ROS is starting; told
#                             "twice", a second one follows 1.5 s later, as
#                             launch's SIGTERM follows its interrupt
STAND_IN = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <semaphore.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

extern void *PyEval_SaveThread(void);

static sem_t made;

static void __attribute__((constructor)) begin(void) { sem_init(&made, 0, 0); }

/* For the Python below: comes back once the call that never returns is made. */
void stand_in_waits_for_the_call(void) {
    while (sem_wait(&made) != 0) continue;
}

static void *the_real(const char *name) {
    static void *rcl;
    if (rcl == NULL) rcl = dlopen("librcl.so", RTLD_NOW);
    return dlsym(rcl, name);
}

static void never_return_if_told(const char *name) {
    const char *which = getenv("STAND_IN_NEVER_RETURNS");
    if (which == NULL || strcmp(which, name) != 0) return;
    if (getenv("STAND_IN_LETS_PYTHON_RUN") != NULL) PyEval_SaveThread();
    sem_post(&made);
    for (;;) pause();
}

int rcl_init(int argc, char const *const *argv, const void *options, void *context) {
    if (getenv("STAND_IN_STOPS_AT_START") != NULL) {
        fputs("stand-in: asked to stop while ROS starts\n", stderr);
        raise(SIGINT);
    }
    return ((int (*)(int, char const *const *, const void *, void *))the_real("rcl_init"))(
        argc, argv, options, context);
}

int rcl_node_init(
    void *node, const char *name, const char *space, void *context, const void *options) {
    const char *stops = getenv("STAND_IN_STOPS_AT_START");
    if (stops != NULL && strcmp(stops, "twice") == 0) {
        struct timespec rest = {1, 500000000};
        while (nanosleep(&rest, &rest) != 0) continue;
        fputs("stand-in: asked to stop again, 1.5 s later\n", stderr);
        raise(SIGTERM);
    }
    return ((int (*)(void *, const char *, const char *, void *, const void *))the_real(
        "rcl_node_init"))(node, name, space, context, options);
}

int rcl_node_fini(void *node) {
    never_return_if_told("rcl_node_fini");
    return ((int (*)(void *))the_real("rcl_node_fini"))(node);
}

int rcl_shutdown(void *context) {
    never_return_if_told("rcl_shutdown");
    return ((int (*)(void *))the_real("rcl_shutdown"))(context);
}
"""

# In front of the node's Python: Python loads a `sitecustomize` at its start,
# before a line of the node runs. Told STAND_IN_STOP_THREAD_IS_LATE, this one
# holds the node's thread named "stop", the one that waits for the stop, from
# the moment the stop wakes it until ROS's call is made. That is the order a
# busy machine brings about now and then: the thread is woken and does not
# get to run. It is held in the library and without Python's lock, as a
# thread is that the machine keeps waiting. It says so when the node starts
# the thread, not when it holds it: on a busy machine the thread may be late
# by itself, and then it says nothing any more.
LATE = r"""
import os
import threading

if "STAND_IN_STOP_THREAD_IS_LATE" in os.environ:
    import ctypes

    library = ctypes.CDLL(os.environ["LD_PRELOAD"])
    start, wait = threading.Thread.start, threading.Event.wait

    def start_and_say_so(thread):
        if thread.name == "stop":
            os.write(2, b"stand-in: the thread that waits for the stop will be late\n")
        start(thread)

    def wait_and_be_late(event, timeout=None):
        woken = wait(event, timeout)
        if threading.current_thread().name == "stop":
            library.stand_in_waits_for_the_call()
        return woken

    threading.Thread.start = start_and_say_so
    threading.Event.wait = wait_and_be_late
"""


@pytest.fixture(scope="module")
def stand_in(tmp_path_factory) -> Path:
    """The library, built with the compiler that built the messages, and the Python beside it."""
    directory = tmp_path_factory.mktemp("stand-in")
    source, library = directory / "stand_in.c", directory / "stand_in.so"
    source.write_text(STAND_IN)
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(library), str(source)], check=True)
    (directory / "sitecustomize.py").write_text(LATE)
    return library


class View:
    """The viewer node with the stand-in in front of ROS, and everything it says."""

    def __init__(self, stand_in: Path, **told: str):
        environment = {"LD_PRELOAD": str(stand_in)}
        environment.update({f"STAND_IN_{name.upper()}": value for name, value in told.items()})
        if "stop_thread_is_late" in told:
            # Python takes `sitecustomize` from the first place that has one.
            environment["PYTHONPATH"] = f"{stand_in.parent}:{os.environ['PYTHONPATH']}"
        self.process = subprocess.Popen(
            ["env", *(f"{name}={value}" for name, value in environment.items()), *COMMAND],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            self.lines.append((time.monotonic(), line))

    def said(self) -> str:
        return "".join(line for _, line in self.lines)

    def says(self, sentence: str, within_s: float = 60.0) -> float:
        """Wait until the node says the sentence, and return when it did."""
        deadline = time.monotonic() + within_s
        while True:
            for at, line in self.lines:
                if sentence in line:
                    return at
            assert self.process.poll() is None, self.said()
            assert time.monotonic() < deadline, self.said()
            time.sleep(0.01)

    def ends(self, asked_at: float) -> tuple[int, float, str]:
        """The end: the exit code, the seconds since the stop was asked for, and all it said."""
        try:
            code = self.process.wait(timeout=20.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise AssertionError(f"Still there 20 s after the stop:\n{self.said()}") from None
        took = time.monotonic() - asked_at
        time.sleep(0.2)  # the reader takes the last lines
        return code, took, self.said()

    def stopped(self) -> tuple[int, float, str]:
        """Ask the running node to stop, as launch does, and wait for the end."""
        self.says(STARTED)
        asked_at = time.monotonic()
        self.process.send_signal(signal.SIGINT)
        return self.ends(asked_at)


def test_a_stop_that_meets_no_such_call_ends_at_once_and_says_nothing(stand_in):
    """Also with the stand-in in front of ROS: told nothing, it passes every call on."""
    code, took, said = View(stand_in).stopped()
    assert code == 0, said
    assert took < 3.0, said
    assert GAVE_UP not in said and LAST_RESORT not in said and "Traceback" not in said, said


@pytest.mark.parametrize("call", [call for call, _ in CALLS])
def test_after_three_seconds_the_node_says_so_and_ends_with_exit_code_0(stand_in, call):
    code, took, said = View(stand_in, never_returns=call, lets_python_run="yes").stopped()
    assert code == 0, said
    assert 2.9 < took < 4.0, said
    assert said.count(GAVE_UP) == 1, said
    assert LAST_RESORT not in said and "Traceback" not in said, said


@pytest.mark.parametrize(("call", "made"), CALLS)
def test_a_call_that_keeps_pythons_lock_is_ended_a_second_later_and_named(stand_in, call, made):
    code, took, said = View(stand_in, never_returns=call).stopped()
    assert code == 1, said
    assert 3.9 < took < 5.0, said
    # Where the main thread stood: in ROS's call, made from the node's `run`.
    assert LAST_RESORT in said and made in said and " in run\n" in said, said
    assert GAVE_UP not in said, said


def test_the_bound_counts_from_the_request_also_while_the_node_is_still_starting(stand_in):
    """A stop right after the start finds the node half built, and is no less of a stop."""
    node = View(stand_in, stops_at_start="yes", never_returns="rcl_node_fini")
    code, took, said = node.ends(asked_at=node.says(ASKED_AT_START))
    assert code == 1, said
    assert 3.9 < took < 5.0, said
    assert LAST_RESORT in said and "in destroy_node" in said and " in run\n" in said, said


def test_the_bound_counts_from_the_first_request_when_a_second_one_follows(stand_in):
    """Launch follows its interrupt with SIGTERM, and the node hears both.

    Between the two the stand-in keeps ROS busy for a second and a half, with
    Python's lock. Neither moves the bound: not the second request, and not
    the wait for the lock, which a thread that had to arm the last resort
    would sit out first.
    """
    node = View(stand_in, stops_at_start="twice", never_returns="rcl_node_fini")
    code, took, said = node.ends(asked_at=node.says(ASKED_AT_START))
    assert ASKED_AGAIN in said, said
    assert code == 1, said
    assert 3.9 < took < 5.0, said
    assert LAST_RESORT in said and "in destroy_node" in said and " in run\n" in said, said


def test_the_last_resort_does_not_wait_for_a_thread_that_comes_too_late(stand_in):
    """Whoever hears the stop arms the last resort, before anything of the stop reaches ROS.

    The thread that waits for the stop cannot do it. Here it is woken and
    runs again only once the first of ROS's two calls is made, and then
    never: the call keeps Python's lock. A last resort that this thread had
    to arm would not be armed, and the node would stay until it is killed.
    """
    node = View(stand_in, never_returns="rcl_node_fini", stop_thread_is_late="yes")
    code, took, said = node.stopped()
    assert WILL_BE_LATE in said, said
    assert code == 1, said
    assert 3.9 < took < 5.0, said
    assert LAST_RESORT in said and "in destroy_node" in said and " in run\n" in said, said
    # Where the thread stood: in the wait it does not come back from.
    assert " in wait_and_be_late\n" in said, said
    assert GAVE_UP not in said, said


def test_nor_does_it_wait_for_that_thread_while_the_node_is_still_starting(stand_in):
    node = View(
        stand_in, stops_at_start="yes", never_returns="rcl_node_fini", stop_thread_is_late="yes"
    )
    code, took, said = node.ends(asked_at=node.says(ASKED_AT_START))
    assert WILL_BE_LATE in said, said
    assert code == 1, said
    assert 3.9 < took < 5.0, said
    assert LAST_RESORT in said and "in destroy_node" in said and " in run\n" in said, said
    assert " in wait_and_be_late\n" in said, said

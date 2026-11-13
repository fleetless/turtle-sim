# SPDX-License-Identifier: Apache-2.0
"""A ROS domain of this package's own, and a bound for each of its tests.

`colcon test` runs packages side by side. On one domain this bringup's view
would show the next package's fake turtle. A caller that chose a domain
keeps it.

A test that does not end would stand until somebody ends the container. So
each one is armed before its fixtures are set up and disarmed after they are
torn down: when it takes longer than its bound, Python's faulthandler writes
where every thread stands and ends the run with exit code 1. Its own thread
needs no lock, so it also ends a test that waits in a ROS call which keeps
Python's; a thread or a signal handler written in Python could not.
`@pytest.mark.stands(seconds)` gives one test another bound, and nothing
else does. pytest's own `faulthandler_timeout` would arm the same timer once
more for every test, without ending the run, and so switch the bound off: a
run that sets it is refused.

The other packages' tests have the same lines. A package's tests are not
installed, so each package keeps its own.
"""

import faulthandler
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("ROS_DOMAIN_ID", "73")

BOUND_S = 180
"""How long a test may take, in seconds.

Measured on one CPU that the test shares with a busy loop. The slowest, the
simulator's services and actions, takes 138 s and asks for 420 s itself. The
next, the bring-up's goal with both pictures, takes 23 s: this is seven
times that.
"""

HERE = Path(__file__).resolve().parent
REAL_STDERR = pytest.StashKey[int]()


def pytest_configure(config):
    try:
        theirs = float(config.getini("faulthandler_timeout") or 0)
    except ValueError:
        theirs = 0  # pytest's faulthandler plugin is switched off, and its option with it
    if theirs > 0:
        raise pytest.UsageError(
            "faulthandler_timeout is set, and these tests refuse it: pytest would arm "
            "faulthandler's one timer again for every test, without ending the run, and a test "
            f"that hangs would stand for ever instead of being ended by the bound of {__file__}."
        )
    config.addinivalue_line(
        "markers", f"stands(seconds): this test's bound, where {BOUND_S} s are not enough"
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item):
    """Around all of one test: its fixtures of every scope, the test, their end.

    A fixture would arm too late and disarm too early. Fixtures of a module's
    scope, which start ROS and the node under test, are set up before it and
    torn down after it; and a launch test takes no fixtures at all.
    """
    if HERE not in item.path.resolve().parents:
        # pytest asks every conftest.py it has loaded, about every test. One
        # of another directory, in a run that holds several, is not this
        # file's to bound.
        yield
        return

    seconds = bound_of(item)

    # Between two tests, file descriptor 2 is the real stderr. While one runs,
    # pytest points it at a file of its own, and nobody reads that file once
    # the run was ended: so the stacks go to a copy, taken here.
    stash = item.config.stash
    if REAL_STDERR not in stash:
        stash[REAL_STDERR] = os.dup(2)
    try:
        faulthandler.dump_traceback_later(seconds, exit=True, file=stash[REAL_STDERR])
    except (TypeError, ValueError, OverflowError):
        raise refusal(item) from None

    # faulthandler writes stacks, not names. Whoever runs the suite may name a
    # directory: the test this run is at stands there, in a file of the
    # package's name, for as long as it runs. `docker/ros-tests.sh` does.
    # Said and noted once the timer is armed, never for a test that was not.
    armed = f"{item.nodeid}: armed for {seconds} s"
    if item.config.getoption("verbose") > 0:
        print(armed, file=sys.stderr, flush=True)
    directory = os.environ.get("TURTLE_TEST_ARMED")
    note = Path(directory, HERE.parent.name) if directory else None
    if note:
        note.write_text(armed + "\n")
    yield
    faulthandler.cancel_dump_traceback_later()
    if note:
        note.unlink()


def bound_of(item):
    """What the test's `stands` marker says, or the usual bound.

    A test carries the marker once, with one number. Anything else is refused
    here, and a number faulthandler cannot wait for where it is armed: both
    by the test's name. Left alone, either ends the run as an internal error
    of pytest's, which says less; and of two markers the nearer one would
    win without a word.
    """
    markers = list(item.iter_markers("stands"))
    if not markers:
        return BOUND_S
    said = markers[0]
    if len(markers) > 1 or len(said.args) != 1 or said.kwargs or isinstance(said.args[0], bool):
        raise refusal(item)
    return said.args[0]


def refusal(item) -> pytest.UsageError:
    return pytest.UsageError(
        f"{item.nodeid}: `stands` takes one positive number of seconds, and a test carries it "
        "once, as in @pytest.mark.stands(300)."
    )

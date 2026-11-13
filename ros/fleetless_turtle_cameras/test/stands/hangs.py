# SPDX-License-Identifier: Apache-2.0
"""Tests that never end, on purpose: what the bound in `../conftest.py` does to them.

No suite collects this directory, because its files are not named
`test_*.py`. Each test is run by hand and alone, since the first one that
stands ends the run. In the workspace a run of the `ros` stage leaves behind:

    TURTLE_TEST_WS=turtle-sim-ws ./run-tests.sh ros
    docker run --rm -v turtle-sim-ws:/ws turtle-sim:test-ros bash -c '
        source /ws/install/setup.bash
        cd /ws/src/turtle-sim/ros/fleetless_turtle_cameras
        python3 -m pytest test/stands/hangs.py::test_that_waits_in_python'

Five seconds later the run is over: `Timeout (0:00:05)!`, where every thread
stands, exit code 1, and no summary from pytest, which was not asked. With
`-v` the line before the stacks names the test.

The last test has no marker and takes the three minutes every test has.
There is no other way to set a bound, so that nothing can switch it off.
"""

import ctypes
import subprocess
import threading

import pytest
from test_cameras_stop import STAND_IN


@pytest.mark.stands(5)
def test_that_waits_in_python():
    threading.Event().wait()


@pytest.mark.stands(5)
def test_that_waits_in_a_call_which_keeps_pythons_lock(tmp_path, monkeypatch):
    """The stop tests' stand-in for ROS's C library, here called as rclpy calls it.

    `PyDLL` keeps Python's lock around the call, and the stand-in was told
    never to return from it: no line of Python runs in this process again.
    """
    source, library = tmp_path / "stand_in.c", tmp_path / "stand_in.so"
    source.write_text(STAND_IN)
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(library), str(source)], check=True)
    monkeypatch.setenv("STAND_IN_NEVER_RETURNS", "rcl_node_fini")
    ctypes.PyDLL(str(library)).rcl_node_fini(None)


@pytest.fixture(scope="module")
def never_set_up():
    threading.Event().wait()


@pytest.mark.stands(5)
def test_whose_module_fixture_is_never_set_up(never_set_up):
    pass


@pytest.fixture(scope="module")
def never_torn_down():
    yield
    threading.Event().wait()


@pytest.mark.stands(5)
def test_whose_module_fixture_is_never_torn_down(never_torn_down):
    pass


@pytest.mark.stands(8)
def test_that_asked_for_eight_seconds_and_gets_them():
    threading.Event().wait()


def test_that_asked_for_nothing_and_gets_three_minutes():
    threading.Event().wait()

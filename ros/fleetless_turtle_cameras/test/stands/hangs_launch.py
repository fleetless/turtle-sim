# SPDX-License-Identifier: Apache-2.0
"""A launch test that never ends, on purpose. `hangs.py` says how to run it:

    python3 -m pytest test/stands/hangs_launch.py

A launch test is one test to pytest, whatever it holds, and takes its bound
from a marker on `generate_test_description`.
"""

import threading
import unittest

import launch
import launch.actions
import launch_testing.actions
import pytest


@pytest.mark.stands(5)
@pytest.mark.launch_test
def generate_test_description():
    # Launch ends a test whose processes are gone. This one outlives the
    # bound and then ends by itself: nobody is left to end it.
    under_test = launch.actions.ExecuteProcess(cmd=["sleep", "60"])
    return launch.LaunchDescription([under_test, launch_testing.actions.ReadyToTest()])


class TestThatStands(unittest.TestCase):
    def test_that_waits_in_python(self):
        threading.Event().wait()

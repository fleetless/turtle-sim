# SPDX-License-Identifier: Apache-2.0
"""The pace: how many fixed steps a caller with a clock should take."""

import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.pace import Pace

DT = c.STEP_S


def run(pace: Pace, times: list[float]) -> list[int]:
    return [pace.due(now) for now in times]


def test_a_punctual_timer_takes_one_step_a_call():
    pace = Pace()
    # A millisecond after each step falls due, as a timer would come.
    times = [100.0] + [100.0 + i * DT + 0.001 for i in range(1, 500)]
    assert run(pace, times) == [1] * 500


def test_the_first_step_is_due_at_once_whatever_the_clock_reads():
    assert Pace().due(123456.789) == 1
    assert Pace().due(0.0) == 1


def test_a_timer_that_jitters_keeps_real_time():
    pace = Pace()
    # Alternately 5 ms early and 5 ms late: never more than a step out.
    times = [10.0 + i * DT + (0.005 if i % 2 else -0.005) for i in range(1, 1001)]
    taken = run(pace, [10.0, *times])
    assert set(taken) <= {0, 1, 2}
    assert sum(taken) == pytest.approx((times[-1] - 10.0) / DT, abs=1.5)


def test_a_slow_timer_makes_up_the_steps_it_missed():
    pace = Pace()
    # Twenty-five calls a second instead of fifty, for ten seconds.
    taken = run(pace, [5.0] + [5.0 + i * 2 * DT + 0.001 for i in range(1, 250)])
    assert taken[0] == 1 and set(taken[1:]) == {2}
    assert sum(taken) == 499  # ten seconds of steps, bar the one still to come


def test_a_call_that_comes_early_takes_no_step():
    pace = Pace()
    assert pace.due(1.0) == 1
    assert pace.due(1.0 + DT / 2) == 0
    assert pace.due(1.0 + DT + 0.001) == 1


def test_a_short_lag_is_made_up_in_one_call():
    pace = Pace()
    assert pace.due(1.0) == 1
    assert pace.due(1.0 + 5 * DT + 0.001) == c.CATCH_UP_STEPS == 5


def test_after_a_long_lag_the_world_goes_on_and_does_not_jump():
    pace = Pace()
    assert pace.due(1.0) == 1
    # Two seconds of nothing: a hundred steps are missing, and stay missing.
    assert pace.due(3.0) == 1
    assert run(pace, [3.0 + i * DT + 0.001 for i in range(1, 51)]) == [1] * 50


def test_the_step_and_the_limit_are_arguments():
    pace = Pace(step_s=0.1, most=2)
    assert pace.due(0.0) == 1
    assert pace.due(0.25) == 2
    assert pace.due(1.0) == 1  # seven steps behind: more than two, so none are made up

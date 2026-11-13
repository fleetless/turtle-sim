# SPDX-License-Identifier: Apache-2.0
"""Markers the bound refuses, on purpose. `hangs.py` says how to run one:

    python3 -m pytest "test/stands/refused.py::test_whose_marker_is_refused[zero]"

The run ends at once with exit code 4 and one sentence that names the test
and says what `stands` takes. The test does not run, and nothing is noted
for whoever started the run: a test that was never armed did not stand.
"""

import pytest

REFUSED = {
    "no number": pytest.mark.stands(),
    "a keyword": pytest.mark.stands(seconds=300),
    "two numbers": pytest.mark.stands(300, 2),
    "zero": pytest.mark.stands(0),
    "less than zero": pytest.mark.stands(-5),
    "a string": pytest.mark.stands("300"),
    "nothing": pytest.mark.stands(None),
    "yes": pytest.mark.stands(True),
    "not a number": pytest.mark.stands(float("nan")),
    "for ever": pytest.mark.stands(float("inf")),
    "three hundred years": pytest.mark.stands(1e10),
    "twice": [pytest.mark.stands(300), pytest.mark.stands(2)],
}


@pytest.mark.parametrize(
    "why", [pytest.param(why, marks=marks, id=why) for why, marks in REFUSED.items()]
)
def test_whose_marker_is_refused(why):
    raise AssertionError(f"This test ran, with a marker that says {why}.")

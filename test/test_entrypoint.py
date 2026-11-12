# SPDX-License-Identifier: Apache-2.0
"""The entrypoint listens for a stop before it does anything else.

Until its trap is set, a stop is lost, and Docker kills the container when
its ten seconds are up. How long that lasts is for a stopwatch and the smoke
test. What stands before the trap can be read here: nothing that could wait.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
TRAP = "trap stop INT TERM"


def statements(script: str) -> list[str]:
    """A script's statements in their order: no comments, a function as its name."""
    found, in_a_function = [], False
    for line in script.splitlines():
        if in_a_function:
            in_a_function = line != "}"
        elif line.strip() and not line.lstrip().startswith("#"):
            function = re.fullmatch(r"(\w+)\(\) \{", line)
            in_a_function = bool(function)
            found.append(f"{function[1]}()" if function else line)
    return found


def test_a_function_is_one_statement_and_a_comment_is_none():
    script = "# a comment\nfirst\n\nstop() {\n    # inside\n    second\n}\ntrap stop INT\n"
    assert statements(script) == ["first", "stop()", "trap stop INT"]


def test_nothing_that_can_wait_stands_before_the_trap():
    before = statements(ENTRYPOINT.read_text())
    before = before[: before.index(TRAP)]
    # How the script fails, and what `stop` has to find when it runs: the
    # container's stderr for the turtle, the two things it reads and writes,
    # and itself. Whatever else the entrypoint does, it does listening.
    assert before == ["set -eo pipefail", "exec 3>&2", "stopping=", "turtle=", "stop()"]

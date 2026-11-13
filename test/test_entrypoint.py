# SPDX-License-Identifier: Apache-2.0
"""The entrypoint listens for a stop before it does anything else.

Until its trap is set, a stop is lost, and Docker kills the container when
its ten seconds are up. How long that lasts is for a stopwatch and the smoke
test. What stands before the trap can be read here: nothing that could wait.

And it decides what a token is, before anything reads the variable. That is
run here, in a copy whose one path of the image points at a stand-in for
the turtle.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

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


# --- The entrypoint, run -------------------------------------------------------

START = "/opt/turtle/start.sh"

# In place of start.sh, which sets up ROS and becomes the launcher: it says
# what it was given for a token, and ends. `exit`, because for a command the
# entrypoint reads this file into its own shell.
TURTLE = """#!/bin/sh
if [ -n "${FLEETLESS_TOKEN+set}" ]; then
    echo "a token: [$FLEETLESS_TOKEN]"
else
    echo "no token"
fi
exit 0
"""

# Without arguments the entrypoint starts the turtle; with some, a command in its place.
THE_TURTLE, A_COMMAND = [], ["ros2", "topic", "list"]


def token_behind_a_copy(directory: Path, token: str | None, arguments: list[str]) -> str:
    """What reaches the turtle of the token the container was started with. None: with none."""
    script = ENTRYPOINT.read_text()
    assert script.count(START) == 2  # once for the turtle, once for a command
    (directory / "start.sh").write_text(TURTLE)
    (directory / "start.sh").chmod(0o755)
    (directory / "entrypoint.sh").write_text(script.replace(START, f"{directory}/start.sh"))
    environment = {name: value for name, value in os.environ.items() if name != "FLEETLESS_TOKEN"}
    if token is not None:
        environment["FLEETLESS_TOKEN"] = token
    ran = subprocess.run(
        ["bash", str(directory / "entrypoint.sh"), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (ran.returncode, ran.stderr) == (0, ""), ran
    return ran.stdout


@pytest.mark.parametrize("arguments", [THE_TURTLE, A_COMMAND], ids=["the turtle", "a command"])
@pytest.mark.parametrize(
    "token",
    [None, "", " ", "   ", "\t", " \t\r\n "],
    ids=["unset", "empty", "a blank", "blanks", "a tab", "blanks of every kind"],
)
def test_a_token_that_is_empty_or_only_blanks_is_no_token(tmp_path, token, arguments):
    """The launch file would take a blank for a token and start the bridge, which refuses it."""
    assert token_behind_a_copy(tmp_path, token, arguments) == "no token\n"


@pytest.mark.parametrize("arguments", [THE_TURTLE, A_COMMAND], ids=["the turtle", "a command"])
@pytest.mark.parametrize("token", ["frt_abc", " frt_abc\t", "frt abc"])
def test_a_real_token_is_passed_on_untouched(tmp_path, token, arguments):
    """Blanks around it included: the bridge strips them."""
    assert token_behind_a_copy(tmp_path, token, arguments) == f"a token: [{token}]\n"

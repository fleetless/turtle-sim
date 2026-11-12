# SPDX-License-Identifier: Apache-2.0
"""The whole bringup, as `ros2 launch` starts it: the nodes side by side.

Each node's own tests start that node alone. What only shows with all of
them: one node that ends takes the others with it, and launch does that
with an interrupt, at whatever moment each of them has reached. However it
goes, the user reads one sentence about what was wrong, and never a stack
trace.
"""

import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

GARDEN = Path(__file__).resolve().parents[3] / "maps" / "garden.png"
NO_TOKEN = "FLEETLESS_TOKEN is not set: simulator and view are running, no bridge was started."

# The race is between two processes starting. A few rounds each, so a lucky
# one does not pass for all.
ROUNDS = range(3)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def bringup(**variables: str) -> subprocess.Popen:
    """`ros2 launch` of the turtle's launch file, without a token."""
    environment = {**os.environ, "TURTLE_MAP": str(GARDEN), **variables}
    environment.pop("FLEETLESS_TOKEN", None)
    return subprocess.Popen(
        ["ros2", "launch", "fleetless_turtle_bringup", "turtle.launch.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )


def ended(process: subprocess.Popen, timeout: float = 60.0) -> str:
    """Wait for the launch to end by itself and return what it said."""
    try:
        said, _ = process.communicate(timeout=timeout)
    finally:
        if process.poll() is None:
            process.kill()
    return said


def assert_one_sentence_and_no_stack_trace(process: subprocess.Popen, said: str, sentence: str):
    assert process.returncode == 1, said
    assert sentence in said, said
    assert said.count("[FATAL]") == 1, said
    assert "Traceback" not in said and "KeyboardInterrupt" not in said, said
    # Promptly: the other node went at the interrupt. Launch did not have to
    # wait its five seconds and reach for SIGTERM.
    assert "escalating to" not in said, said
    assert "SIGTERM" not in said and "SIGKILL" not in said, said


@pytest.mark.parametrize("attempt", ROUNDS)
def test_a_view_port_that_is_no_port_ends_everything_with_one_sentence(attempt):
    process = bringup(TURTLE_VIEW_PORT="abc")
    said = ended(process)
    assert_one_sentence_and_no_stack_trace(
        process,
        said,
        'TURTLE_VIEW_PORT (the parameter port) is "abc": '
        "the view listens on a whole number from 1 to 65535.",
    )


@pytest.mark.parametrize("attempt", ROUNDS)
def test_a_view_port_in_use_ends_everything_with_one_sentence(attempt):
    with socket.socket() as taken:
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        port = taken.getsockname()[1]
        process = bringup(TURTLE_VIEW_PORT=str(port))
        said = ended(process)
    assert_one_sentence_and_no_stack_trace(
        process, said, f"Port {port} is in use: the view cannot listen there."
    )


@pytest.mark.parametrize("attempt", ROUNDS)
def test_a_map_that_does_not_exist_ends_everything_with_one_sentence(attempt):
    # The other way round: here the simulator gives up and the view is told to go.
    process = bringup(TURTLE_MAP="/nowhere/garden.png", TURTLE_VIEW_PORT=str(free_port()))
    said = ended(process)
    assert_one_sentence_and_no_stack_trace(process, said, "Map /nowhere/garden.png does not exist.")


@pytest.mark.parametrize(
    ("variables", "sentence"),
    [
        (
            {"TURTLE_BATTERY_DRAIN": "abc"},
            'TURTLE_BATTERY_DRAIN (the parameter battery_drain) is "abc": '
            "the battery drain factor is a number of 0 or more.",
        ),
        ({"TURTLE_MAP": ""}, "TURTLE_MAP (the parameter map) is empty: it names the map file."),
        ({"TURTLE_MAP": "/tmp"}, "Map /tmp is a directory: a map is one PNG file."),
    ],
    ids=["a drain factor that is no number", "an empty map", "a directory as the map"],
)
def test_a_refusal_names_the_variable_the_user_set(variables, sentence):
    process = bringup(**{"TURTLE_VIEW_PORT": str(free_port()), **variables})
    said = ended(process)
    assert_one_sentence_and_no_stack_trace(process, said, sentence)


def test_without_a_token_every_node_runs_and_an_interrupt_ends_them_cleanly():
    port = free_port()
    process = bringup(TURTLE_VIEW_PORT=str(port))
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True)
    reader.start()
    expected = [
        NO_TOKEN,
        f"The view listens on port {port}.",
        "The turtle is on the pad, cell (8, 8)",
        "The cameras draw top (480 x 480) and eye (320 x 240)",
    ]
    try:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if all(any(text in line for line in lines) for text in expected):
                break
            assert process.poll() is None, "".join(lines)
            time.sleep(0.1)
        else:
            raise AssertionError("The bringup did not come up:\n" + "".join(lines))
        process.send_signal(signal.SIGINT)
        process.wait(timeout=20.0)
    finally:
        if process.poll() is None:
            process.kill()
    reader.join(timeout=5.0)
    said = "".join(lines)
    assert process.returncode == 0, said
    assert "Traceback" not in said and "KeyboardInterrupt" not in said, said
    assert "escalating to" not in said, said
    assert said.count("process has finished cleanly") == 3, said

# SPDX-License-Identifier: Apache-2.0
"""What the simulator node refuses at start: one sentence, exit 2, no stack trace.

And what it accepts: a drain factor in whatever type a launch file hands
over, and a request to stop.
"""

import signal
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
import rclpy
from ament_index_python.packages import get_package_prefix
from fleetless_turtle_msgs.action import DriveTo, DriveToSquare
from probe import Probe

GARDEN = Path(__file__).resolve().parents[3] / "maps" / "garden.png"
SIMULATOR = (
    Path(get_package_prefix("fleetless_turtle_sim")) / "lib" / "fleetless_turtle_sim" / "simulator"
)


def simulator(*parameters: str) -> list[str]:
    arguments = [str(SIMULATOR), "--ros-args"]
    for parameter in parameters:
        arguments += ["-p", parameter]
    return arguments


def refused(*parameters: str) -> str:
    """Start the node, expect it to refuse, and return what it said."""
    done = subprocess.run(simulator(*parameters), capture_output=True, text=True, timeout=60)
    said = done.stdout + done.stderr
    assert done.returncode == 2, said
    assert "Traceback" not in said, said
    lines = [line for line in said.splitlines() if line.strip()]
    assert len(lines) == 1, said
    assert "[FATAL]" in lines[0] and "[turtle_simulator]" in lines[0]
    return lines[0]


def test_a_map_that_does_not_exist():
    assert refused("map:=/nowhere/garden.png").endswith("Map /nowhere/garden.png does not exist.")


def test_no_map_at_all():
    sentence = "TURTLE_MAP (the parameter map) is empty: it names the map file."
    assert refused().endswith(sentence)
    assert refused("map:=''").endswith(sentence)


def test_a_directory_as_the_map(tmp_path):
    assert refused(f"map:={tmp_path}").endswith(
        f"Map {tmp_path} is a directory: a map is one PNG file."
    )


def test_a_map_of_another_size(tmp_path):
    path = tmp_path / "small.png"
    cv2.imwrite(str(path), np.full((64, 64, 3), 255, np.uint8))
    assert refused(f"map:={path}").endswith("is 64 x 64 pixels; a map is exactly 256 x 256.")


def test_a_map_with_a_colour_that_is_none_of_the_five(tmp_path):
    pixels = cv2.imread(str(GARDEN), cv2.IMREAD_UNCHANGED)
    pixels[40, 12] = (0x7F, 0x7F, 0x7F)
    path = tmp_path / "smudged.png"
    cv2.imwrite(str(path), pixels)
    sentence = refused(f"map:={path}")
    assert "a pixel of colour #7f7f7f at column 12, row 40 from the top left" in sentence


@pytest.mark.parametrize("value", ["-1.0", "abc", ".nan", "true", "-0.5"])
def test_a_battery_drain_that_is_negative_or_no_number(value):
    sentence = refused(f"map:={GARDEN}", f"battery_drain:={value}")
    # The variable a `docker run` user set comes first, the parameter after.
    assert 'TURTLE_BATTERY_DRAIN (the parameter battery_drain) is "' in sentence
    assert sentence.endswith("the battery drain factor is a number of 0 or more.")


def started(*parameters: str) -> tuple[subprocess.Popen, list[str]]:
    """Start the node and wait until it says where the turtle is.

    Not for a fixed time: the node's start takes four seconds on one busy
    core, and a signal during its imports ends it at once and quietly, which
    is right, and not what these tests are after.
    """
    process = subprocess.Popen(
        simulator(*parameters), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True)
    reader.start()
    deadline = time.monotonic() + 60.0
    while not any("The turtle is on the pad, cell (8, 8)" in line for line in lines):
        assert process.poll() is None, "".join(lines)
        assert time.monotonic() < deadline, "".join(lines)
        time.sleep(0.05)
    return process, lines


def stopped(process: subprocess.Popen, lines: list[str], how: signal.Signals) -> str:
    """Send the signal, wait for the end and return everything the node said."""
    time.sleep(0.5)
    assert process.poll() is None, "".join(lines)  # it keeps running until told
    process.send_signal(how)
    try:
        process.wait(timeout=20.0)
    finally:
        if process.poll() is None:
            process.kill()
    time.sleep(0.2)  # the reader takes the last lines
    return "".join(lines)


@pytest.mark.parametrize("value", ["0.5", "2", "0", "'0.25'"])
def test_a_battery_drain_of_any_usable_type_starts_and_a_signal_ends_it(value):
    process, lines = started(f"map:={GARDEN}", f"battery_drain:={value}")
    said = stopped(process, lines, signal.SIGINT)
    assert process.returncode == 0, said
    assert "Traceback" not in said, said


def test_sigterm_ends_it_too():
    process, lines = started(f"map:={GARDEN}")
    said = stopped(process, lines, signal.SIGTERM)
    assert process.returncode == 0, said
    assert "Traceback" not in said, said


@pytest.mark.parametrize("how", [signal.SIGINT, signal.SIGTERM])
def test_a_stop_while_goals_run_and_wait_ends_it_cleanly_too(how):
    """A goal's callback is a coroutine that waits for its goal to end. A stop
    comes first here: for one goal that runs and one that was preempted and
    answered. Nothing waits for them, and nothing complains about them."""
    process, lines = started(f"map:={GARDEN}")
    rclpy.init()
    probe = Probe("stopper")
    try:
        first = probe.send(DriveToSquare, "/turtle/drive_to_square", square="H8")
        second = probe.send(DriveTo, "/turtle/drive_to", x=240, y=240)
        assert first.result(timeout=10.0)[1].reason == "preempted"
        time.sleep(2.0)
        assert not second.ended
        said = stopped(process, lines, how)
    finally:
        probe.close()
        rclpy.shutdown()
    assert process.returncode == 0, said
    assert "Traceback" not in said and "never awaited" not in said, said
    assert "rror" not in said, said

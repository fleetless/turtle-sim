# SPDX-License-Identifier: Apache-2.0
"""What the simulator node refuses at start: one sentence, exit 2, no stack trace.

And what it accepts: a drain factor in whatever type a launch file hands
over, and a request to stop.
"""

import signal
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from ament_index_python.packages import get_package_prefix

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


@pytest.mark.parametrize("value", ["0.5", "2", "0", "'0.25'"])
def test_a_battery_drain_of_any_usable_type_starts_and_a_signal_ends_it(value):
    process = subprocess.Popen(
        simulator(f"map:={GARDEN}", f"battery_drain:={value}"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=3.0)  # it keeps running
        process.send_signal(signal.SIGINT)
        said, _ = process.communicate(timeout=10.0)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, said
    assert "Traceback" not in said, said
    assert "The turtle is on the pad, cell (8, 8)" in said


def test_sigterm_ends_it_too():
    process = subprocess.Popen(
        simulator(f"map:={GARDEN}"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=3.0)
        process.send_signal(signal.SIGTERM)
        said, _ = process.communicate(timeout=10.0)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, said
    assert "Traceback" not in said, said

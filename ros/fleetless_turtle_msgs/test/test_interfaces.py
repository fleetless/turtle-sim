# SPDX-License-Identifier: Apache-2.0
"""The seven interfaces, as `ros2 interface show` prints them once built.

The lists below are the contract: the shipped fleetless.yaml, the view and
every client name these fields. A field that changes here changes there.
"""

import subprocess

import pytest

DRIVE_RESULT_AND_FEEDBACK = [
    "---",
    "bool success",
    "string reason",
    "string message",
    "int32 cell_x",
    "int32 cell_y",
    "float32 distance_m",
    "float32 duration_s",
    "---",
    "string phase",
    "int32 cell_x",
    "int32 cell_y",
    "float32 remaining_m",
    "float32 progress",
]

INTERFACES = {
    "msg/TurtleStatus": [
        "std_msgs/Header header",
        "int32 cell_x",
        "int32 cell_y",
        "string square",
        "float32 heading_deg",
        "float32 speed",
        "float32 turn_rate",
        "string mode",
        "bool crawling",
        "bool on_charger",
        "bool bumper",
        "uint32 collisions",
        "float64 odometer_m",
        "bool trail",
    ],
    "msg/World": [
        "std_msgs/Header header",
        "uint32 width",
        "uint32 height",
        "float32 resolution",
        "uint8 FREE=0",
        "uint8 WALL=1",
        "uint8 ROCK=2",
        "uint8 WATER=3",
        "uint8 PAD=4",
        "uint8[] kinds",
    ],
    "srv/Teleport": [
        "int32 x",
        "int32 y",
        "float32 heading_deg",
        "---",
        "bool success",
        "string message",
    ],
    "srv/SetBlock": [
        "int32 x",
        "int32 y",
        "bool occupied",
        "---",
        "bool success",
        "string message",
    ],
    "action/DriveTo": ["int32 x", "int32 y", *DRIVE_RESULT_AND_FEEDBACK],
    "action/DriveToSquare": ["string square", *DRIVE_RESULT_AND_FEEDBACK],
    "action/Dock": [
        "---",
        "bool success",
        "string reason",
        "string message",
        "float32 battery_percent",
        "float32 duration_s",
        "---",
        "string phase",
        "float32 battery_percent",
        "float32 remaining_m",
    ],
}


def shown(name: str) -> list[str]:
    """The interface's own lines: no comments, no expanded nested fields."""
    out = subprocess.run(
        ["ros2", "interface", "show", "--no-comments", f"fleetless_turtle_msgs/{name}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    # A nested message's fields are printed indented below its own line, and
    # `--no-comments` leaves the `#` of an inline comment behind.
    own = [line.split("#")[0] for line in out.splitlines() if line and not line[0].isspace()]
    return [" ".join(line.split()) for line in own if line.strip()]


@pytest.mark.parametrize("name", sorted(INTERFACES))
def test_interface_is_what_the_spec_writes(name):
    assert shown(name) == INTERFACES[name]


def test_no_interface_is_missing_from_this_file():
    out = subprocess.run(
        ["ros2", "interface", "package", "fleetless_turtle_msgs"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    # Building an action also generates its goal, result and feedback
    # services and messages; those carry an underscore.
    built = {line.strip().removeprefix("fleetless_turtle_msgs/") for line in out.splitlines()}
    assert {name for name in built if name and "_" not in name.split("/")[1]} == set(INTERFACES)

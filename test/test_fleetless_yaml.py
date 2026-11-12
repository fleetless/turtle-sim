# SPDX-License-Identifier: Apache-2.0
"""The shipped fleetless.yaml against the simulator it describes.

The schema check is scripts/validate-fleetless-yaml.mjs. This file holds
what no schema knows: that the bounds in the file are the simulator's own,
that the entries are the ones the turtle really offers, and that each has a
description.
"""

import math
import re
from pathlib import Path

import pytest
import yaml

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core import joints
from fleetless_turtle_sim.core.goals import REASONS
from fleetless_turtle_sim.core.world import square_bounds

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "fleetless" / "turtle.fleetless.yaml"
INTERFACES = ROOT / "ros" / "fleetless_turtle_msgs"
MSGS = INTERFACES / "msg"

STATUS = "fleetless_turtle_msgs/msg/TurtleStatus"

# Slug: (topic, type, field, throttled to Hz). No field is the whole message.
DATAPOINTS = {
    "battery_soc": ("/battery_state", "sensor_msgs/msg/BatteryState", "percentage", None),
    "on_charger": ("/turtle/status", STATUS, "on_charger", None),
    "crawling": ("/turtle/status", STATUS, "crawling", None),
    "bumper": ("/turtle/status", STATUS, "bumper", None),
    "cell_x": ("/turtle/status", STATUS, "cell_x", 2),
    "cell_y": ("/turtle/status", STATUS, "cell_y", 2),
    "square": ("/turtle/status", STATUS, "square", 2),
    "heading": ("/turtle/status", STATUS, "heading_deg", 2),
    "speed": ("/turtle/status", STATUS, "speed", 2),
    "mode": ("/turtle/status", STATUS, "mode", 2),
    "collisions": ("/turtle/status", STATUS, "collisions", 2),
    "odometer": ("/turtle/status", STATUS, "odometer_m", None),
    "range_front": ("/range/front", "sensor_msgs/msg/Range", "range", None),
    "range_left": ("/range/left", "sensor_msgs/msg/Range", "range", None),
    "range_right": ("/range/right", "sensor_msgs/msg/Range", "range", None),
    "scan": ("/scan", "sensor_msgs/msg/LaserScan", None, 2),
    "joint_states": ("/joint_states", "sensor_msgs/msg/JointState", None, None),
}

# Slug: (topic, width, height, kbit/s).
CAMERAS = {
    "top": ("/turtle/camera/top/image_raw", 480, 480, 800),
    "eye": ("/turtle/camera/eye/image_raw", 320, 240, 500),
}


# Slug: (ROS name, type, the request's or the goal's fields).
SERVICES = {
    "reset": ("/turtle/reset", "std_srvs/srv/Trigger", None),
    "teleport": ("/turtle/teleport", "fleetless_turtle_msgs/srv/Teleport", "srv/Teleport.srv"),
    "set_block": ("/turtle/set_block", "fleetless_turtle_msgs/srv/SetBlock", "srv/SetBlock.srv"),
    "set_trail": ("/turtle/set_trail", "std_srvs/srv/SetBool", None),
}
ACTIONS = {
    "drive_to": (
        "/turtle/drive_to",
        "fleetless_turtle_msgs/action/DriveTo",
        "action/DriveTo.action",
    ),
    "drive_to_square": (
        "/turtle/drive_to_square",
        "fleetless_turtle_msgs/action/DriveToSquare",
        "action/DriveToSquare.action",
    ),
    "dock": ("/turtle/dock", "fleetless_turtle_msgs/action/Dock", "action/Dock.action"),
}

CELL = {"type": "int32", "min_value": 0, "max_value": c.GRID_CELLS - 1}
# The ROS fields a parameter of another name fills.
PARAMETER_OF = {"x": "cell_x", "y": "cell_y"}


def fields_of(definition: str, part: int = 0) -> dict[str, str]:
    """The fields of one part of a .srv or .action file, name to type: 0 is the request or goal."""
    text = (INTERFACES / definition).read_text().split("\n---\n")[part]
    return {name: kind for kind, name in re.findall(r"^(\w+) (\w+)", text, re.MULTILINE)}


def flat(text: str) -> str:
    return " ".join(text.split())


def bounds(parameter: dict) -> dict:
    """A parameter without its description."""
    return {key: value for key, value in parameter.items() if key != "description"}


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load(CONFIG.read_text())


@pytest.fixture(scope="module")
def entries(config):
    """Every entry of every section, by slug. No slug is used twice."""
    sections = ("datapoints", "publishers", "services", "actions", "cameras")
    merged = {}
    for section in sections:
        assert not set(config[section]) & set(merged), section
        merged.update(config[section])
    return merged


def test_it_is_a_version_1_document_for_any_organisation(config, entries):
    assert config["fleetless"] == 1
    # No role and no access section: it publishes into any organisation.
    assert "access" not in config
    assert not [slug for slug, entry in entries.items() if "role" in entry]
    assert set(config) == {
        "fleetless",
        "datapoints",
        "publishers",
        "services",
        "actions",
        "cameras",
    }


def test_the_datapoints_are_the_ones_the_turtle_offers(config):
    described = {
        slug: (entry["topic"], entry["type"], entry.get("field"), entry.get("rate_throttle_hz"))
        for slug, entry in config["datapoints"].items()
    }
    assert described == DATAPOINTS


def test_every_status_field_named_exists_in_the_message(config):
    fields = set(re.findall(r"^\w+ (\w+)", (MSGS / "TurtleStatus.msg").read_text(), re.MULTILINE))
    for slug, entry in config["datapoints"].items():
        if entry["type"] == STATUS:
            assert entry["field"] in fields, slug


def test_every_entry_and_every_parameter_has_a_description(entries):
    for slug, entry in entries.items():
        assert len(entry.get("description", "").strip()) >= 40, slug
        for name, parameter in entry.get("parameters", {}).items():
            assert len(parameter.get("description", "").strip()) >= 20, f"{slug}.{name}"


def test_the_battery_is_a_percentage_with_a_past_and_two_alerts(config):
    battery = config["datapoints"]["battery_soc"]
    assert battery["numeric"]["scale"] == 100 and battery["numeric"]["unit"] == "%"
    assert battery["retention"]["enabled"] is True
    assert (battery["chart"]["y_min"], battery["chart"]["y_max"]) == (0, 100)
    low, empty = battery["alerts"]["battery_low"], battery["alerts"]["battery_empty"]
    assert (low["condition"], low["severity"]) == ({"fire_at": 20, "resolve_at": 25}, "warning")
    assert (empty["condition"], empty["severity"]) == ({"fire_at": 1, "resolve_at": 5}, "error")
    # The empty alert lets go where crawling does.
    assert empty["condition"]["resolve_at"] == c.CRAWL_END_PERCENT


def test_the_other_alerts(config):
    datapoints = config["datapoints"]
    assert datapoints["crawling"]["alerts"]["crawling"]["condition"] == {"fire_at": True}
    ahead = datapoints["range_front"]["alerts"]["obstacle_ahead"]["condition"]
    assert ahead == {"fire_at": 0.3, "resolve_at": 0.5}
    assert c.RANGE_MIN_M < ahead["fire_at"] < ahead["resolve_at"] < c.RANGE_MAX_M
    with_alerts = {slug for slug, entry in datapoints.items() if "alerts" in entry}
    assert with_alerts == {"battery_soc", "crawling", "range_front"}


def test_units_where_there_is_one(config):
    units = {
        slug: entry["numeric"]["unit"]
        for slug, entry in config["datapoints"].items()
        if "unit" in entry.get("numeric", {})
    }
    assert units == {
        "battery_soc": "%",
        "heading": "°",
        "speed": "m/s",
        "odometer": "m",
        "range_front": "m",
        "range_left": "m",
        "range_right": "m",
    }
    assert config["datapoints"]["odometer"]["retention"]["enabled"] is True


def test_the_drive_publishers_bounds_are_the_simulators_limits(config):
    assert set(config["publishers"]) == {"drive"}
    drive = config["publishers"]["drive"]
    assert (drive["topic"], drive["type"]) == ("/cmd_vel", "geometry_msgs/msg/Twist")
    linear, angular = drive["parameters"]["linear_speed"], drive["parameters"]["angular_speed"]
    assert (linear["min_value"], linear["max_value"]) == (
        -c.LINEAR_SPEED_LIMIT,
        c.LINEAR_SPEED_LIMIT,
    )
    assert (angular["min_value"], angular["max_value"]) == (
        -c.ANGULAR_SPEED_LIMIT,
        c.ANGULAR_SPEED_LIMIT,
    )
    assert linear["type"] == angular["type"] == "float64"
    # A caller may name only one of the two; the other stands still.
    assert linear["default"] == angular["default"] == 0.0


def test_only_the_two_fields_a_differential_drive_follows_are_open(config):
    drive = config["publishers"]["drive"]
    assert drive["message"] == {
        "linear": {"x": "${linear_speed}", "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": "${angular_speed}"},
    }


def test_the_failsafe_is_a_zero_twist_after_500_ms(config):
    drive = config["publishers"]["drive"]
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    assert drive["failsafe"] == {"timeout_ms": 500, "message": {"linear": zero, "angular": zero}}
    assert drive["quiet_timeout_ms"] == 2000
    # The bridge's stop and the simulator's own wait the same half second.
    assert drive["failsafe"]["timeout_ms"] == c.COMMAND_TIMEOUT_S * 1000


def test_the_numbers_in_the_descriptions_are_the_simulators(config):
    """A description is read by a model that then acts on it. It must not lie."""
    datapoints = config["datapoints"]
    text = " ".join(datapoints["battery_soc"]["description"].split())
    assert "Standing empties a full battery in an hour" in text
    assert c.BATTERY_STANDING_S == 3600
    assert "driving at full speed in five minutes" in text and c.BATTERY_DRIVING_S == 300
    assert "full again after one minute" in text and c.BATTERY_CHARGING_S == 60
    assert "a tenth of its speed" in text and c.CRAWL_LINEAR_FACTOR == 0.1
    assert "back at 5 %" in text and c.CRAWL_END_PERCENT == 5

    crawling = " ".join(datapoints["crawling"]["description"].split())
    assert "a tenth of its linear speed, a quarter of its turn rate" in crawling
    assert (c.CRAWL_LINEAR_FACTOR, c.CRAWL_ANGULAR_FACTOR) == (0.1, 0.25)

    speed = " ".join(datapoints["speed"]["description"].split())
    assert f"At most {c.LINEAR_SPEED_LIMIT} either way" in speed
    assert f"{c.LINEAR_SPEED_LIMIT * c.CRAWL_LINEAR_FACTOR:.2f} while it crawls" in speed

    for side in ("front", "left", "right"):
        reach = " ".join(datapoints[f"range_{side}"]["description"].split())
        assert f"{c.RANGE_MIN_M} to {c.RANGE_MAX_M}" in reach, side

    scan = " ".join(datapoints["scan"]["description"].split())
    assert f"{c.LASER_RAYS} distances" in scan and "5 degrees apart" in scan
    assert f"within {c.LASER_RANGE_MAX_M} m is null" in scan
    # Index 0 looks straight back, index 36 straight ahead.
    assert pytest.approx(0.0) == c.LASER_ANGLE_MIN + 36 * c.LASER_ANGLE_INCREMENT
    assert "index 36 straight ahead" in scan and "index 0 looks straight back" in scan

    drive = " ".join(config["publishers"]["drive"]["description"].split())
    assert "good for half a second" in drive and c.COMMAND_TIMEOUT_S == 0.5
    assert f"{c.LINEAR_ACCELERATION_LIMIT:g} m/s²" in drive
    assert f"{c.ANGULAR_ACCELERATION_LIMIT:g} rad/s²" in drive

    bumper = " ".join(datapoints["bumper"]["description"].split())
    assert f"within the last {c.BUMPER_HOLD_S} s" in bumper
    mode = " ".join(datapoints["mode"]["description"].split())
    assert f"within the last {c.COMMAND_TIMEOUT_S} s" in mode


def test_the_cameras_are_the_two_the_turtle_has(config):
    described = {
        slug: (
            entry["source"]["topic"],
            entry["width"],
            entry["height"],
            entry["bitrate_kbps"],
        )
        for slug, entry in config["cameras"].items()
    }
    assert described == CAMERAS
    for slug, entry in config["cameras"].items():
        assert entry["source"] == {
            "kind": "ros",
            "topic": CAMERAS[slug][0],
            "type": "sensor_msgs/msg/Image",
        }, slug
        assert entry["snapshot_interval_seconds"] == 5, slug


def test_the_cameras_sizes_and_rate_are_the_renderers(config):
    """The bridge resizes what does not fit, and a rate above the cameras' is a promise not kept."""
    top, eye = config["cameras"]["top"], config["cameras"]["eye"]
    assert (top["width"], top["height"]) == (look.TOP_SIZE_PX, look.TOP_SIZE_PX)
    assert (eye["width"], eye["height"]) == (look.EYE_WIDTH_PX, look.EYE_HEIGHT_PX)
    assert top["fps"] == eye["fps"] == look.FRAME_RATE_HZ


def test_the_numbers_in_the_cameras_descriptions_are_the_renderers(config):
    top = " ".join(config["cameras"]["top"]["description"].split())
    assert f"a window of {look.TOP_WINDOW_M:g} m by {look.TOP_WINDOW_M:g} m" in top
    metres_a_pixel = look.TOP_WINDOW_M / look.TOP_SIZE_PX
    assert f"{metres_a_pixel:g} m a pixel" in top
    pixels_a_cell = round(c.CELL_SIZE_M / metres_a_pixel)
    assert f"a cell is {pixels_a_cell} pixels and a square {pixels_a_cell * c.SQUARE_CELLS}" in top

    eye = " ".join(config["cameras"]["eye"]["description"].split())
    assert f"from {look.EYE_HEIGHT_M} m above the floor" in eye
    assert f"{math.degrees(look.EYE_FIELD_OF_VIEW):g} degrees wide" in eye
    assert f"up to {look.EYE_RANGE_M:g} m" in eye
    assert f"a cream face {look.WALL_HEIGHT_M} m tall" in eye
    assert "a rock a grey one half as tall" in eye
    assert look.ROCK_HEIGHT_M == look.WALL_HEIGHT_M / 2
    assert f"the cells, {c.CELL_SIZE_M} m each" in eye


def test_joint_states_is_the_whole_message_at_the_rate_the_turtle_publishes_it(config):
    entry = config["datapoints"]["joint_states"]
    # No field: the console's Model page takes names and positions together.
    # No throttle: 20 Hz is what moves a model smoothly, and the most a
    # datapoint may carry.
    assert "field" not in entry and "rate_throttle_hz" not in entry
    assert c.JOINT_RATE_HZ == 20.0
    text = " ".join(entry["description"].split())
    for name in joints.NAMES:
        assert f"`{name}`" in text, name
    assert len(joints.NAMES) == 5 and "five joints" in text
    assert f"by {c.HEAD_TURN_LIMIT:.2f} rad (30 degrees) at most" in text
    assert math.degrees(c.HEAD_TURN_LIMIT) == pytest.approx(30.0)
    assert f"by {c.LEG_SWING} rad either way" in text
    assert f"once for every {c.LEG_STRIDE_M} m their side has walked" in text


def test_the_page_colours_the_battery_where_the_alert_fires(config):
    panel = (ROOT / "ros" / "fleetless_turtle_viewer" / "web" / "panel.js").read_text()
    low = float(re.search(r"^const BATTERY_LOW_PERCENT = ([0-9.]+);", panel, re.MULTILINE)[1])
    assert (
        low == config["datapoints"]["battery_soc"]["alerts"]["battery_low"]["condition"]["fire_at"]
    )


# --- Services and actions ---------------------------------------------------


def test_the_services_and_actions_are_the_ones_the_turtle_offers(config):
    for section, expected in (("services", SERVICES), ("actions", ACTIONS)):
        described = {
            slug: (entry["ros_name"], entry["type"]) for slug, entry in config[section].items()
        }
        assert described == {slug: (name, kind) for slug, (name, kind, _) in expected.items()}
    # The node has them under these names, and the definitions exist.
    node = (ROOT / "ros/fleetless_turtle_sim/fleetless_turtle_sim/node.py").read_text()
    for name, kind, definition in [*SERVICES.values(), *ACTIONS.values()]:
        assert f'"{name.removeprefix("/")}"' in node, name
        if definition is not None:
            assert (INTERFACES / definition).is_file()
            assert kind == f"fleetless_turtle_msgs/{definition.rsplit('.', 1)[0]}"


def test_every_message_fills_every_field_of_its_request_from_a_parameter(config):
    for section, expected in (("services", SERVICES), ("actions", ACTIONS)):
        for slug, (_, _, definition) in expected.items():
            entry = config[section][slug]
            if definition is None:
                continue
            fields = fields_of(definition)
            # Each field from the parameter of its own name, nothing fixed and
            # nothing left out. A cell's two fields are the exception: a
            # parameter's name has two letters at least, so `x` is `cell_x`.
            named = {name: PARAMETER_OF.get(name, name) for name in fields}
            assert entry["message"] == {name: f"${{{named[name]}}}" for name in fields}, slug
            assert set(entry["parameters"]) == set(named.values()), slug
            for name, kind in fields.items():
                assert entry["parameters"][named[name]]["type"] == kind, f"{slug}.{name}"
    assert "message" not in config["services"]["reset"]
    assert "parameters" not in config["services"]["reset"]
    trail = config["services"]["set_trail"]
    assert trail["message"] == {"data": "${record}"}
    assert bounds(trail["parameters"]["record"]) == {"type": "bool"}


def test_a_cell_is_0_to_255_wherever_one_is_named(config):
    for entry in (
        config["services"]["teleport"],
        config["services"]["set_block"],
        config["actions"]["drive_to"],
    ):
        for name in ("cell_x", "cell_y"):
            parameter = entry["parameters"][name]
            assert bounds(parameter) == CELL
            # One cell, one name: what goes in here is what the datapoint reports.
            assert f"The same count the datapoint {name} reports." in parameter["description"]
            assert config["datapoints"][name]["field"] == name


def test_the_other_parameters_have_the_specs_bounds_and_defaults(config):
    heading = config["services"]["teleport"]["parameters"]["heading_deg"]
    assert bounds(heading) == {"type": "float32", "min_value": 0, "max_value": 360, "default": 0}
    occupied = config["services"]["set_block"]["parameters"]["occupied"]
    assert bounds(occupied) == {"type": "bool", "default": True}
    target = config["actions"]["dock"]["parameters"]["target_percent"]
    assert bounds(target) == {
        "type": "float32",
        "min_value": c.DOCK_TARGET_MIN_PERCENT,
        "max_value": c.DOCK_TARGET_MAX_PERCENT,
        "default": 100,
    }


def test_the_squares_pattern_takes_the_names_the_turtle_takes_and_no_other(config):
    square = config["actions"]["drive_to_square"]["parameters"]["square"]
    assert bounds(square) == {"type": "string", "regex": "^[A-P]([1-9]|1[0-6])$"}
    # The cloud compiles it as a JavaScript pattern, where `$` is the end and
    # nothing but the end. Anchored on both sides, it is the whole value.
    pattern = square["regex"]
    assert pattern.startswith("^") and pattern.endswith("$")
    whole = re.compile(pattern.removeprefix("^").removesuffix("$"))
    names = [f"{letter}{number}" for letter in "ABCDEFGHIJKLMNOP" for number in range(1, 17)]
    assert len(names) == c.SQUARES * c.SQUARES
    for name in names:
        assert whole.fullmatch(name) and square_bounds(name) is not None, name
    strangers = [
        "Z9",
        "A0",
        "A17",
        "Q1",
        "a1",
        "",
        "A1 ",
        " A1",
        "A01",
        "AA1",
        "A1\n",
        "P160",
        "1A",
    ]
    for name in strangers:
        assert not whole.fullmatch(name) and square_bounds(name) is None, repr(name)


def test_the_numbers_in_the_goals_descriptions_are_the_simulators(config):
    drive = flat(config["actions"]["drive_to"]["description"])
    assert f"keeps {c.PLANNER_CLEARANCE_M} m of room" in drive
    assert f"at up to {c.LINEAR_SPEED_LIMIT} m/s" in drive
    assert "Feedback comes twice a second" in drive and c.GOAL_FEEDBACK_RATE_HZ == 2
    assert "ends the goal within 2 s" in drive
    # Braking from full speed and from a full turn takes well under that.
    assert c.LINEAR_SPEED_LIMIT / c.LINEAR_ACCELERATION_LIMIT < 2.0
    assert c.ANGULAR_SPEED_LIMIT / c.ANGULAR_ACCELERATION_LIMIT < 2.0

    dock = flat(config["actions"]["dock"]["description"])
    assert "a full charge from empty takes one minute" in dock and c.BATTERY_CHARGING_S == 60
    assert "Feedback comes twice a second" in dock

    trail = flat(config["services"]["set_trail"]["description"])
    assert f"a point every {c.TRAIL_SPACING_M} m, the newest {c.TRAIL_MAX_POINTS}" in trail

    mode = flat(config["datapoints"]["mode"]["description"])
    for word in ("docking", "auto", "teleop", "charging", "idle"):
        assert f"`{word}`" in mode


def test_a_description_names_what_its_messages_really_hold(config):
    for slug, phases in (
        ("drive_to", "planning, driving, replanning"),
        ("dock", "driving, charging"),
    ):
        definition = ACTIONS[slug][2]
        description = flat(config["actions"][slug]["description"])
        assert f"`phase` ({phases})" in description
        # The phases are the ones the action's own definition names.
        assert phases.replace(", ", " | ") in (INTERFACES / definition).read_text()
        for part in (1, 2):  # the result, the feedback
            for field in fields_of(definition, part):
                if field not in ("cell_x", "cell_y"):
                    assert f"`{field}`" in description, f"{slug}: {field}"


def test_every_reason_a_description_names_is_one_the_turtle_gives(config):
    text = CONFIG.read_text()
    # The comment above the actions lists them all, with what becomes of the goal.
    for reason in REASONS:
        assert re.search(rf"^#   {reason} ", text, re.MULTILINE), reason
    listed = set(re.findall(r"^#   (\w+) +\S.*(?:succeeded|aborted|canceled)$", text, re.MULTILINE))
    assert listed == set(REASONS)
    for slug in ("drive_to", "dock"):
        # Without what stands in brackets behind a reason, to explain it.
        description = re.sub(r" \([^)]*\)", "", flat(config["actions"][slug]["description"]))
        named = re.search(r"Reasons: ([a-z_, ]+)\.", description)[1].split(", ")
        assert len(named) >= 8, named
        assert set(named) <= set(REASONS), slug
        # What cannot happen to it is not named, and what the cloud refuses
        # before the turtle hears of it is not either.
        assert "out_of_range" not in named
        assert ("charged" in named) == (slug == "dock")
        assert ("reached" in named) == (slug == "drive_to")


def test_the_counters_say_that_a_reset_starts_them_again(config):
    for slug in ("odometer", "collisions"):
        description = flat(config["datapoints"][slug]["description"])
        assert "since the simulator started or was last reset" in description, slug
    status = (MSGS / "TurtleStatus.msg").read_text()
    assert status.count("since start or reset") == 2

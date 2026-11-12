# SPDX-License-Identifier: Apache-2.0
"""The shipped fleetless.yaml against the simulator it describes.

The schema check is scripts/validate-fleetless-yaml.mjs. This file holds
what no schema knows: that the bounds in the file are the simulator's own,
that the entries are the ones the turtle really offers, and that each has a
description.
"""

import re
from pathlib import Path

import pytest
import yaml

from fleetless_turtle_sim.core import constants as c

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "fleetless" / "turtle.fleetless.yaml"
MSGS = ROOT / "ros" / "fleetless_turtle_msgs" / "msg"

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
}


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load(CONFIG.read_text())


def test_it_is_a_version_1_document_for_any_organisation(config):
    assert config["fleetless"] == 1
    # No role and no access section: it publishes into any organisation.
    assert "access" not in config
    entries = {**config["datapoints"], **config["publishers"]}
    assert not [slug for slug, entry in entries.items() if "role" in entry]
    assert set(config) == {"fleetless", "datapoints", "publishers"}


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


def test_every_entry_and_every_parameter_has_a_description(config):
    entries = {**config["datapoints"], **config["publishers"]}
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


def test_the_page_colours_the_battery_where_the_alert_fires(config):
    panel = (ROOT / "ros" / "fleetless_turtle_viewer" / "web" / "panel.js").read_text()
    low = float(re.search(r"^const BATTERY_LOW_PERCENT = ([0-9.]+);", panel, re.MULTILINE)[1])
    assert (
        low == config["datapoints"]["battery_soc"]["alerts"]["battery_low"]["condition"]["fire_at"]
    )

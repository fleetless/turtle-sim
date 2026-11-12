# SPDX-License-Identifier: Apache-2.0
"""The battery: each rate, the drain factor, charging, and crawling."""

import math

import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.battery import Battery

DT = c.STEP_S


def run(battery: Battery, seconds: float, linear=0.0, angular=0.0, on_pad=False) -> None:
    for _ in range(round(seconds / DT)):
        battery.step(DT, linear, angular, on_pad)


def test_it_starts_full():
    battery = Battery()
    assert battery.percent == 100.0 and not battery.crawling
    assert battery.drain == 1.0


# --- The three discharge rates ----------------------------------------------


def test_standing_empties_it_in_an_hour():
    battery = Battery()
    run(battery, 1800)
    assert battery.percent == pytest.approx(50.0)
    run(battery, 1799)
    assert battery.percent == pytest.approx(100 / 3600)
    run(battery, 1)
    assert battery.percent == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("linear", [0.5, -0.5])
def test_full_linear_speed_empties_it_in_five_minutes(linear):
    battery = Battery()
    run(battery, 150, linear=linear)
    assert battery.percent == pytest.approx(50.0)
    run(battery, 150, linear=linear)
    assert battery.percent == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("angular", [2.0, -2.0])
def test_turning_on_the_spot_empties_it_in_ten_minutes(angular):
    battery = Battery()
    run(battery, 300, angular=angular)
    assert battery.percent == pytest.approx(50.0)
    run(battery, 300, angular=angular)
    assert battery.percent == pytest.approx(0.0, abs=1e-6)


def test_the_part_beyond_standing_grows_with_the_speed():
    # Half speed costs standing plus half of what full speed adds.
    half = Battery()
    run(half, 100, linear=0.25)
    assert 100 - half.percent == pytest.approx(100 * (100 / 3600 + 0.5 * (100 / 300 - 100 / 3600)))
    quarter_turn = Battery()
    run(quarter_turn, 100, angular=0.5)
    assert 100 - quarter_turn.percent == pytest.approx(
        100 * (100 / 3600 + 0.25 * (100 / 600 - 100 / 3600))
    )


def test_the_three_parts_add_up():
    battery = Battery()
    run(battery, 100, linear=0.5, angular=2.0)
    standing, driving, turning = 100 / 3600, 100 / 300 - 100 / 3600, 100 / 600 - 100 / 3600
    assert 100 - battery.percent == pytest.approx(100 * (standing + driving + turning))


def test_a_speed_beyond_the_limit_costs_no_more_than_the_limit():
    battery, fast = Battery(), Battery()
    run(battery, 60, linear=0.5, angular=2.0)
    run(fast, 60, linear=5.0, angular=20.0)
    assert fast.percent == pytest.approx(battery.percent)


def test_one_charge_lasts_150_metres_at_full_speed():
    battery = Battery()
    seconds = 0.0
    while battery.percent > 0.0:
        battery.step(DT, c.LINEAR_SPEED_LIMIT, 0.0, False)
        seconds += DT
    assert seconds * c.LINEAR_SPEED_LIMIT == pytest.approx(150.0, abs=0.02)


# --- The drain factor -------------------------------------------------------


def test_the_drain_factor_multiplies_the_discharge():
    double = Battery(drain=2.0)
    run(double, 900)
    assert double.percent == pytest.approx(50.0)
    tenth = Battery(drain=0.1)
    run(tenth, 300, linear=0.5)
    assert tenth.percent == pytest.approx(90.0)


def test_a_drain_factor_of_zero_turns_discharge_off():
    battery = Battery(drain=0.0)
    run(battery, 3600, linear=0.5, angular=2.0)
    assert battery.percent == 100.0


@pytest.mark.parametrize("drain", [-1.0, -0.001, math.nan, math.inf, -math.inf])
def test_a_drain_factor_that_is_negative_or_no_number_is_refused(drain):
    with pytest.raises(ValueError, match="is not a number of 0 or more"):
        Battery(drain=drain)


# --- Charging ---------------------------------------------------------------


def test_on_the_pad_it_is_full_after_a_minute():
    battery = Battery()
    battery.percent = 0.0
    run(battery, 30, on_pad=True)
    assert battery.percent == pytest.approx(50.0)
    run(battery, 30, on_pad=True)
    assert battery.percent == pytest.approx(100.0)
    run(battery, 30, on_pad=True)
    assert battery.percent == 100.0


def test_on_the_pad_nothing_discharges_whatever_the_turtle_does():
    moving, standing = Battery(), Battery()
    moving.percent = standing.percent = 10.0
    run(moving, 20, linear=0.5, angular=2.0, on_pad=True)
    run(standing, 20, on_pad=True)
    assert moving.percent == pytest.approx(standing.percent)
    assert moving.percent == pytest.approx(10.0 + 20 * 100 / 60)


def test_charging_is_not_scaled_by_the_drain_factor():
    battery = Battery(drain=10.0)
    battery.percent = 0.0
    run(battery, 60, on_pad=True)
    assert battery.percent == pytest.approx(100.0)


def test_it_never_leaves_0_to_100():
    battery = Battery()
    run(battery, 400, linear=0.5)
    assert battery.percent == 0.0
    run(battery, 100, on_pad=True)
    assert battery.percent == 100.0


def test_refill():
    battery = Battery()
    run(battery, 400, linear=0.5)
    battery.refill()
    assert battery.percent == 100.0 and not battery.crawling


# --- Crawling ---------------------------------------------------------------


def test_crawling_starts_at_empty_and_ends_at_five_percent():
    battery = Battery()
    run(battery, 299, linear=0.5)
    assert battery.percent > 0.0 and not battery.crawling
    run(battery, 2, linear=0.5)
    assert battery.percent == 0.0 and battery.crawling
    # 2.9 s on the pad: 4.83 %, still crawling. Three seconds: 5 %, done.
    run(battery, 2.9, on_pad=True)
    assert battery.percent == pytest.approx(2.9 * 100 / 60) and battery.crawling
    run(battery, 0.2, on_pad=True)
    assert battery.percent >= 5.0 and not battery.crawling


def test_between_empty_and_five_percent_it_keeps_what_it_was_doing():
    battery = Battery()
    battery.percent = 3.0
    run(battery, 1)
    assert not battery.crawling  # it came from above
    run(battery, 200)
    assert battery.percent == 0.0 and battery.crawling
    run(battery, 1.8, on_pad=True)  # 3 %
    run(battery, 10)  # off the pad again, below five
    assert 0.0 < battery.percent < 5.0 and battery.crawling  # it came from below


# --- What the battery reports -----------------------------------------------


def test_the_voltage_is_linear_from_10_to_12_6():
    battery = Battery()
    assert battery.voltage == pytest.approx(12.6)
    battery.percent = 50.0
    assert battery.voltage == pytest.approx(11.3)
    battery.percent = 0.0
    assert battery.voltage == pytest.approx(10.0)


def test_the_supply_status():
    battery = Battery()
    assert battery.supply(on_pad=False) == "discharging"
    assert battery.supply(on_pad=True) == "full"
    battery.percent = 99.0
    assert battery.supply(on_pad=True) == "charging"
    assert battery.supply(on_pad=False) == "discharging"

"""test_schedule_validation: what a schedule may be, before it is sent."""

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.powerocean_single_phase.const import DOMAIN
from custom_components.powerocean_single_phase.coordinator import validate_schedule
from custom_components.powerocean_single_phase.mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_USE_BATTERY,
    Schedule,
)

MAX_POWER = 3000


def hm(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def schedule(**changes: int | bool) -> Schedule:
    """Return a valid daily schedule: charge, 3000 W, 06:00-08:00."""
    values: dict[str, int | bool] = {
        "schedule_id": 12,
        "schedule_type": SCHEDULE_CHARGE,
        "enabled": True,
        "power": MAX_POWER,
        "start": hm(6),
        "end": hm(8),
        "repeat": 1,
        "days": 0,
    }
    return Schedule(**(values | changes))  # type: ignore[arg-type]


def refused(error: pytest.ExceptionInfo[HomeAssistantError]) -> str:
    assert error.value.translation_domain == DOMAIN
    assert error.value.translation_key is not None
    return error.value.translation_key


@pytest.mark.parametrize(
    "valid",
    [
        schedule(),
        schedule(schedule_type=SCHEDULE_USE_BATTERY),
        schedule(power=1),
        # Over midnight, and the whole range of the day
        schedule(start=hm(23), end=hm(2)),
        schedule(start=0, end=hm(23, 30)),
        schedule(start=hm(23, 30), end=0),
        # Chosen days: any mask from Monday (1) to all seven (127)
        schedule(repeat=2, days=1),
        schedule(repeat=2, days=31),
        schedule(repeat=2, days=127),
    ],
)
def test_valid_schedules_pass(valid: Schedule) -> None:
    validate_schedule(valid, MAX_POWER)


@pytest.mark.parametrize("schedule_type", [0, 3, -1])
def test_type_must_be_known(schedule_type: int) -> None:
    with pytest.raises(HomeAssistantError) as error:
        validate_schedule(schedule(schedule_type=schedule_type), MAX_POWER)

    assert refused(error) == "schedule_invalid_type"


def test_without_the_max_power_option_nothing_is_accepted() -> None:
    with pytest.raises(HomeAssistantError) as error:
        validate_schedule(schedule(), None)

    assert refused(error) == "max_schedule_power_not_set"


def test_the_option_is_needed_even_when_the_power_is_not_checked() -> None:
    with pytest.raises(HomeAssistantError) as error:
        validate_schedule(schedule(), None, check_power=False)

    assert refused(error) == "max_schedule_power_not_set"


@pytest.mark.parametrize("power", [0, -5])
def test_power_must_be_at_least_one_watt(power: int) -> None:
    with pytest.raises(HomeAssistantError) as error:
        validate_schedule(schedule(power=power), MAX_POWER)

    assert refused(error) == "schedule_invalid_power"


def test_power_may_not_exceed_the_option() -> None:
    validate_schedule(schedule(power=MAX_POWER), MAX_POWER)

    with pytest.raises(HomeAssistantError) as error:
        validate_schedule(schedule(power=MAX_POWER + 1), MAX_POWER)

    assert refused(error) == "schedule_power_too_high"
    assert error.value.translation_placeholders == {
        "power": "3001",
        "max_power": "3000",
    }


def test_power_is_not_checked_when_it_is_not_changed() -> None:
    # An old schedule above a lowered option can still be edited
    validate_schedule(schedule(power=9999), MAX_POWER, check_power=False)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (hm(6, 15), hm(8)),  # not a step of 30 minutes
        (hm(6), hm(8, 1)),
        (hm(6), hm(6)),  # no time at all
        (-30, hm(8)),
        (hm(6), 1440),  # 24:00 is not a time of the day
        (hm(6), 1470),
    ],
)
def test_times_are_steps_of_30_minutes_within_the_day(start: int, end: int) -> None:
    with pytest.raises(HomeAssistantError) as error:
        validate_schedule(schedule(start=start, end=end), MAX_POWER)

    assert refused(error) == "schedule_invalid_time"


@pytest.mark.parametrize(
    ("repeat", "days"),
    [(1, 5), (2, 0), (2, 128), (2, -1), (3, 1), (65, 0)],
)
def test_days_are_daily_or_a_mask_of_chosen_days(repeat: int, days: int) -> None:
    with pytest.raises(HomeAssistantError) as error:
        validate_schedule(schedule(repeat=repeat, days=days), MAX_POWER)

    assert refused(error) == "schedule_invalid_days"


def test_days_are_not_checked_when_they_are_not_changed() -> None:
    # Older schedules show repeat 65, whose meaning is unknown: kept as it is
    validate_schedule(schedule(repeat=65, days=0), MAX_POWER, check_days=False)

"""test_schedules: names, attributes and service input of the scheduled tasks."""

from datetime import time

import pytest

from custom_components.powerocean_single_phase.mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_USE_BATTERY,
    Schedule,
)
from custom_components.powerocean_single_phase.schedules import (
    ScheduleInputError,
    day_names,
    days_mask,
    format_minutes,
    schedule_attributes,
    schedule_changes,
    schedule_title,
)


def hm(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def schedule(**changes: int | bool) -> Schedule:
    """Return the charge schedule 9: 02:00-06:00, 3000 W, daily, on."""
    values: dict[str, int | bool] = {
        "schedule_id": 9,
        "schedule_type": SCHEDULE_CHARGE,
        "enabled": True,
        "power": 3000,
        "start": hm(2),
        "end": hm(6),
        "repeat": 1,
        "days": 0,
    }
    return Schedule(**(values | changes))  # type: ignore[arg-type]


def test_format_minutes() -> None:
    assert format_minutes(0) == "00:00"
    assert format_minutes(hm(2)) == "02:00"
    assert format_minutes(hm(23, 30)) == "23:30"


def test_title_says_what_the_schedule_does() -> None:
    assert schedule_title(schedule()) == "Charge 02:00-06:00 3000 W"
    assert (
        schedule_title(
            schedule(schedule_type=SCHEDULE_USE_BATTERY, start=hm(23), end=hm(2))
        )
        == "Use battery 23:00-02:00 3000 W"
    )


def test_title_of_an_unknown_type() -> None:
    assert schedule_title(schedule(schedule_type=7)) == "Schedule 02:00-06:00 3000 W"


def test_day_names_and_mask() -> None:
    assert days_mask(["mon", "fri"]) == 1 + 16
    assert days_mask(["sat", "sun"]) == 96
    assert days_mask(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]) == 127
    assert day_names(31) == ["mon", "tue", "wed", "thu", "fri"]
    assert day_names(96) == ["sat", "sun"]
    assert day_names(0) == []


def test_attributes() -> None:
    assert schedule_attributes(schedule(in_use=True)) == {
        "id": 9,
        "type": "charge",
        "start": "02:00",
        "end": "06:00",
        "power": 3000,
        "days": "daily",
        "in_use": True,
    }


def test_attributes_with_chosen_days() -> None:
    attributes = schedule_attributes(
        schedule(schedule_type=SCHEDULE_USE_BATTERY, repeat=2, days=31)
    )

    assert attributes["type"] == "use_battery"
    assert attributes["days"] == ["mon", "tue", "wed", "thu", "fri"]


def test_older_schedules_with_repeat_65_show_as_daily() -> None:
    # The app shows them as Daily
    assert schedule_attributes(schedule(repeat=65))["days"] == "daily"


def test_service_input_becomes_the_arguments_of_the_coordinator() -> None:
    assert schedule_changes(
        {
            "type": "use_battery",
            "power": 3000.0,
            "start": time(23, 0),
            "end": time(2, 30),
            "days": ["mon", "sun"],
            "enabled": False,
        }
    ) == {
        "schedule_type": SCHEDULE_USE_BATTERY,
        "power": 3000,
        "start": hm(23),
        "end": hm(2, 30),
        "days": 65,
        "enabled": False,
    }


def test_service_input_only_has_what_was_given() -> None:
    assert schedule_changes({"power": 1000}) == {"power": 1000}
    assert schedule_changes({}) == {}


def test_daily_means_days_0() -> None:
    assert schedule_changes({"daily": True}) == {"days": 0}


def test_daily_false_changes_nothing() -> None:
    assert schedule_changes({"daily": False}) == {}


def test_days_and_daily_exclude_each_other() -> None:
    with pytest.raises(ScheduleInputError) as error:
        schedule_changes({"days": ["mon"], "daily": True})

    assert error.value.key == "schedule_days_and_daily"


def test_a_time_with_seconds_is_refused() -> None:
    with pytest.raises(ScheduleInputError) as error:
        schedule_changes({"start": time(2, 0, 30)})

    assert error.value.key == "schedule_invalid_time"


@pytest.mark.parametrize("power", [1000.5, 0.1])
def test_power_must_be_whole_watts(power: float) -> None:
    with pytest.raises(ScheduleInputError) as error:
        schedule_changes({"power": power})

    assert error.value.key == "schedule_invalid_power"

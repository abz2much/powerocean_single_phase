"""
schedules.py: names, attributes and service input of the scheduled tasks.

The title says what a schedule does ("Charge 02:00-06:00 3000 W"); the
attributes are what the switches and the sensor show; the service input of
the actions (names of days, times, ...) becomes the arguments of the
coordinator.

This module does not depend on Home Assistant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_REPEAT_DAYS,
    SCHEDULE_USE_BATTERY,
    Schedule,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import time

# Type of a schedule: key in the actions and attributes, and title
TYPE_KEYS = {SCHEDULE_CHARGE: "charge", SCHEDULE_USE_BATTERY: "use_battery"}
TYPE_BY_KEY = {key: number for number, key in TYPE_KEYS.items()}
TYPE_TITLES = {SCHEDULE_CHARGE: "Charge", SCHEDULE_USE_BATTERY: "Use battery"}
# Days of the week as the system has them: Monday 1 to Sunday 64
DAY_BITS = {"mon": 1, "tue": 2, "wed": 4, "thu": 8, "fri": 16, "sat": 32, "sun": 64}


# Keys of the error texts for wrong input of an action
KEY_INVALID_POWER = "schedule_invalid_power"
KEY_INVALID_TIME = "schedule_invalid_time"
KEY_DAYS_AND_DAILY = "schedule_days_and_daily"


class ScheduleInputError(ValueError):
    """The input of an action is wrong; `key` is the key of the error text."""

    def __init__(self, key: str) -> None:
        """Keep the key of the error text."""
        super().__init__(key)
        self.key = key


def format_minutes(minutes: int) -> str:
    """Return minutes since midnight as HH:MM."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def schedule_title(schedule: Schedule) -> str:
    """Return what a schedule does, e.g. "Charge 02:00-06:00 3000 W"."""
    kind = TYPE_TITLES.get(schedule.schedule_type, "Schedule")
    return (
        f"{kind} {format_minutes(schedule.start)}-{format_minutes(schedule.end)} "
        f"{schedule.power} W"
    )


def days_mask(names: Iterable[str]) -> int:
    """Return the bit mask of days given by name (mon ... sun)."""
    mask = 0
    for name in names:
        mask |= DAY_BITS[name]
    return mask


def day_names(mask: int) -> list[str]:
    """Return the names of the days in a bit mask, Monday first."""
    return [name for name, bit in DAY_BITS.items() if mask & bit]


def schedule_days(schedule: Schedule) -> str | list[str]:
    """
    Return "daily" or the chosen days.

    The app shows the older schedules with repeat 65 as Daily.
    """
    if schedule.repeat != SCHEDULE_REPEAT_DAYS:
        return "daily"
    return day_names(schedule.days)


def schedule_attributes(schedule: Schedule) -> dict[str, Any]:
    """Return the attributes of the switch of a schedule."""
    return {
        "id": schedule.schedule_id,
        "type": TYPE_KEYS.get(schedule.schedule_type, f"type_{schedule.schedule_type}"),
        "start": format_minutes(schedule.start),
        "end": format_minutes(schedule.end),
        "power": schedule.power,
        "days": schedule_days(schedule),
        "in_use": schedule.in_use,
    }


def _minutes(value: time) -> int:
    if value.second or value.microsecond:
        raise ScheduleInputError(KEY_INVALID_TIME)
    return value.hour * 60 + value.minute


def schedule_changes(data: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return the arguments of the coordinator for the input of an action.

    Only what was given is returned. `days` is a list of names (an empty list
    means daily); `daily` is the same as an empty list and excludes `days`.
    Raises ScheduleInputError for a power that is not whole watts, a time
    with seconds, or days together with daily.
    """
    changes: dict[str, Any] = {}
    if "type" in data:
        changes["schedule_type"] = TYPE_BY_KEY[data["type"]]
    if "power" in data:
        if float(data["power"]) != int(data["power"]):
            raise ScheduleInputError(KEY_INVALID_POWER)
        changes["power"] = int(data["power"])
    for name in ("start", "end"):
        if name in data:
            changes[name] = _minutes(data[name])
    if data.get("daily"):
        if "days" in data:
            raise ScheduleInputError(KEY_DAYS_AND_DAILY)
        changes["days"] = 0
    elif "days" in data:
        changes["days"] = days_mask(data["days"])
    if "enabled" in data:
        changes["enabled"] = bool(data["enabled"])
    return changes

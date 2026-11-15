"""test_mqtt_schedules: the scheduled tasks in the coordinator, with a fake system."""

import asyncio
from collections.abc import Callable, Generator
from dataclasses import replace
from datetime import timedelta
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.powerocean_single_phase.const import DOMAIN
from custom_components.powerocean_single_phase.mqtt import (
    SCHEDULE_ADD,
    SCHEDULE_CHARGE,
    SCHEDULE_USE_BATTERY,
    Schedule,
)
from custom_components.powerocean_single_phase.tests.integration.test_mqtt import (
    CREDENTIALS,
    RESPONSE,
    SN,
    USER_ID,
    FakeMqtt,
)

MAX_POWER = 6000
TIMEOUT = "custom_components.powerocean_single_phase.coordinator.COMMAND_TIMEOUT"


def hm(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def listed(
    schedule_id: int,
    schedule_type: int,
    power: int,
    start: int,
    end: int,
    **changes: int | bool | None,
) -> Schedule:
    """Return a schedule as the system lists it (on, daily, not in use)."""
    values: dict[str, int | bool | None] = {
        "schedule_id": schedule_id,
        "schedule_type": schedule_type,
        "enabled": True,
        "power": power,
        "start": start,
        "end": end,
        "repeat": 1,
        "days": 0,
        "in_use": False,
        "unknown_2": 2,
    }
    return Schedule(**(values | changes))  # type: ignore[arg-type]


def system_schedules() -> list[Schedule]:
    return [
        listed(10, SCHEDULE_USE_BATTERY, 5000, hm(8, 30), hm(9), in_use=True),
        # Over midnight, with the repeat 65 of an older schedule
        listed(11, SCHEDULE_USE_BATTERY, 2000, hm(23), hm(2), repeat=65),
        listed(12, SCHEDULE_CHARGE, 1500, hm(6), hm(8, 30), enabled=False),
    ]


class ScheduleMqtt(FakeMqtt):
    """The fake client with a system that holds schedules."""

    system: ClassVar[list[Schedule]] = []
    answer_lists: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Count the requests and record the commands."""
        super().__init__(*args, **kwargs)
        self.list_requests = 0
        self.commands: list[tuple[str, Any]] = []

    def request_schedules(self) -> None:
        """Answer with the schedules of the system (paho's thread)."""
        self.list_requests += 1
        if ScheduleMqtt.answer_lists:
            self.callbacks.on_schedules(list(ScheduleMqtt.system))

    def _answer(
        self, seq: int, change: Callable[[list[Schedule]], list[Schedule]]
    ) -> None:
        if FakeMqtt.acknowledge:
            ScheduleMqtt.system = change(list(ScheduleMqtt.system))
            self.callbacks.on_ack(seq)

    def set_schedule_enabled(self, schedule_id: int, enabled: int, seq: int) -> None:
        """Switch a schedule."""
        self.commands.append(("switch", (schedule_id, enabled)))
        self._answer(
            seq,
            lambda schedules: [
                replace(s, enabled=bool(enabled)) if s.schedule_id == schedule_id else s
                for s in schedules
            ],
        )

    def save_schedule(self, action: int, schedule: Schedule, seq: int) -> None:
        """Add or edit a schedule."""
        self.commands.append(
            ("add" if action == SCHEDULE_ADD else "edit", schedule),
        )
        if action == SCHEDULE_ADD:
            self._answer(seq, lambda schedules: [*schedules, schedule])
        else:
            self._answer(
                seq,
                lambda schedules: [
                    replace(schedule, in_use=s.in_use, unknown_2=s.unknown_2)
                    if s.schedule_id == schedule.schedule_id
                    else s
                    for s in schedules
                ],
            )

    def delete_schedule(self, schedule_id: int, seq: int) -> None:
        """Delete a schedule."""
        self.commands.append(("delete", schedule_id))
        self._answer(
            seq,
            lambda schedules: [s for s in schedules if s.schedule_id != schedule_id],
        )


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


@pytest.fixture
def cloud() -> Generator[None]:
    """Login, HTTP response and MQTT credentials; the fake client with schedules."""

    async def login(api: Any) -> None:
        api.user_id = USER_ID

    FakeMqtt.instances.clear()
    FakeMqtt.acknowledge = True
    ScheduleMqtt.system = system_schedules()
    ScheduleMqtt.answer_lists = True
    with (
        patch(
            "custom_components.powerocean_single_phase.ecoflow.HAEcoflowApi.async_authorize",
            autospec=True,
            side_effect=login,
        ),
        patch(
            "custom_components.powerocean_single_phase.ecoflow.HAEcoflowApi.fetch_raw",
            return_value=RESPONSE,
        ),
        patch(
            "custom_components.powerocean_single_phase.ecoflow.HAEcoflowApi.async_mqtt_credentials",
            return_value=CREDENTIALS,
        ),
        patch(
            "custom_components.powerocean_single_phase.coordinator.PowerOceanMqtt",
            ScheduleMqtt,
        ),
    ):
        yield


async def _setup(
    hass: HomeAssistant, *, max_power: int | None = MAX_POWER, mqtt: bool = True
) -> MockConfigEntry:
    options: dict[str, Any] = {
        "friendly_name": "PowerOcean",
        "scan_interval": 10,
        "mqtt": mqtt,
    }
    if max_power is not None:
        options["max_schedule_power"] = max_power
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=5,
        unique_id=f"PowerOcean {SN}",
        title="PowerOcean",
        data={
            "device_id": SN,
            "email": "test@example.com",
            "password": "password123",
            "model_id": "83",
        },
        options=options,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _client() -> ScheduleMqtt:
    (client,) = FakeMqtt.instances
    assert isinstance(client, ScheduleMqtt)
    return client


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _key(error: pytest.ExceptionInfo[HomeAssistantError]) -> str | None:
    assert error.value.translation_domain == DOMAIN
    return error.value.translation_key


# -----------------------------
# Reading
# -----------------------------


async def test_schedules_are_read_when_mqtt_starts(
    hass: HomeAssistant, cloud: None
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    assert _client().list_requests == 1
    assert coordinator.schedules == {s.schedule_id: s for s in system_schedules()}
    assert coordinator.active_schedule == system_schedules()[0]


async def test_no_schedule_in_use(hass: HomeAssistant, cloud: None) -> None:
    ScheduleMqtt.system = [replace(s, in_use=False) for s in system_schedules()]

    entry = await _setup(hass)

    assert entry.runtime_data.active_schedule is None


async def test_schedules_are_polled_every_60_seconds(
    hass: HomeAssistant, cloud: None, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass)

    await _tick(hass, freezer, 59)
    assert _client().list_requests == 1

    await _tick(hass, freezer, 1)
    assert _client().list_requests == 2

    await _tick(hass, freezer, 60)
    assert _client().list_requests == 3


async def test_a_change_of_the_system_shows_with_the_next_poll(
    hass: HomeAssistant, cloud: None, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    # 08:30:22: the system moves "in use" from 10 to 11 by itself
    ScheduleMqtt.system = [
        replace(s, in_use=s.schedule_id == 11) for s in system_schedules()
    ]

    await _tick(hass, freezer, 60)

    assert coordinator.active_schedule is not None
    assert coordinator.active_schedule.schedule_id == 11


async def test_only_a_changed_list_notifies(hass: HomeAssistant, cloud: None) -> None:
    entry = await _setup(hass)
    notified: list[None] = []
    entry.runtime_data.async_add_listener(lambda: notified.append(None))
    callback = _client().callbacks.on_schedules

    await hass.async_add_executor_job(callback, system_schedules())
    await hass.async_block_till_done()
    assert notified == []

    changed = [replace(s, in_use=s.schedule_id == 11) for s in system_schedules()]
    await hass.async_add_executor_job(callback, changed)
    await hass.async_block_till_done()
    assert len(notified) == 1


async def test_the_list_of_the_app_is_taken_over(
    hass: HomeAssistant, cloud: None
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    new = [listed(20, SCHEDULE_CHARGE, 1000, hm(1), hm(2))]

    await hass.async_add_executor_job(_client().callbacks.on_schedules, new)
    await hass.async_block_till_done()

    assert list(coordinator.schedules.values()) == new


async def test_a_single_schedule_of_the_app_is_taken_over(
    hass: HomeAssistant, cloud: None
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    changed = replace(system_schedules()[0], power=4000)

    await hass.async_add_executor_job(_client().callbacks.on_schedule, changed)
    await hass.async_block_till_done()

    assert coordinator.schedules[10] == changed
    assert coordinator.schedules[11] == system_schedules()[1]


async def test_no_schedules_without_the_mqtt_option(
    hass: HomeAssistant, cloud: None
) -> None:
    entry = await _setup(hass, mqtt=False)

    assert not FakeMqtt.instances
    assert entry.runtime_data.schedules == {}
    with pytest.raises(HomeAssistantError) as error:
        await entry.runtime_data.async_set_schedule_enabled(12, enabled=True)
    assert _key(error) == "mqtt_not_connected"


async def test_unload_stops_the_poll_and_clears_the_schedules(
    hass: HomeAssistant, cloud: None, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    client = _client()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await _tick(hass, freezer, 120)

    assert client.list_requests == 1
    assert coordinator.schedules == {}


async def test_a_late_list_after_unload_is_ignored(
    hass: HomeAssistant, cloud: None
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    client = _client()
    assert await hass.config_entries.async_unload(entry.entry_id)

    await hass.async_add_executor_job(client.callbacks.on_schedules, system_schedules())
    await hass.async_block_till_done()

    assert coordinator.schedules == {}


# -----------------------------
# Switching and deleting
# -----------------------------


async def test_switch_a_schedule_on_and_off(hass: HomeAssistant, cloud: None) -> None:
    coordinator = (await _setup(hass)).runtime_data

    await coordinator.async_set_schedule_enabled(12, enabled=True)

    assert _client().commands == [("switch", (12, 1))]
    assert coordinator.schedules[12].enabled is True
    # The list is read again after the command
    assert _client().list_requests == 2

    await coordinator.async_set_schedule_enabled(12, enabled=False)

    assert _client().commands[-1] == ("switch", (12, 0))
    assert coordinator.schedules[12].enabled is False


async def test_switching_needs_no_max_power_option(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass, max_power=None)).runtime_data

    await coordinator.async_set_schedule_enabled(12, enabled=True)
    await coordinator.async_delete_schedule(11)

    assert [name for name, _ in _client().commands] == ["switch", "delete"]


async def test_delete_a_schedule(hass: HomeAssistant, cloud: None) -> None:
    coordinator = (await _setup(hass)).runtime_data

    await coordinator.async_delete_schedule(11)

    assert _client().commands == [("delete", 11)]
    assert list(coordinator.schedules) == [10, 12]


@pytest.mark.parametrize(
    "command",
    [
        lambda c: c.async_set_schedule_enabled(99, enabled=True),
        lambda c: c.async_delete_schedule(99),
        lambda c: c.async_update_schedule(99, power=1000),
    ],
    ids=["switch", "delete", "update"],
)
async def test_an_unknown_schedule_is_refused(
    hass: HomeAssistant, cloud: None, command: Callable[[Any], Any]
) -> None:
    coordinator = (await _setup(hass)).runtime_data

    with pytest.raises(HomeAssistantError) as error:
        await command(coordinator)

    assert _key(error) == "schedule_not_found"
    assert error.value.translation_placeholders == {"schedule_id": "99"}
    assert _client().commands == []


async def test_a_command_without_acknowledgement_fails_and_changes_nothing(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass)).runtime_data
    FakeMqtt.acknowledge = False

    with patch(TIMEOUT, 0.01), pytest.raises(HomeAssistantError) as error:
        await coordinator.async_set_schedule_enabled(12, enabled=True)

    assert _key(error) == "not_acknowledged"
    assert coordinator.schedules[12].enabled is False
    # No new reading after a command that was not acknowledged
    assert _client().list_requests == 1


async def test_a_missing_answer_to_the_new_reading_is_no_error(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass)).runtime_data
    ScheduleMqtt.answer_lists = False

    with patch(TIMEOUT, 0.01):
        await coordinator.async_set_schedule_enabled(12, enabled=True)

    # The command was acknowledged; the state follows with the next poll
    assert _client().commands == [("switch", (12, 1))]
    assert coordinator.schedules[12].enabled is False


# -----------------------------
# Adding
# -----------------------------


async def test_add_takes_the_next_free_id(hass: HomeAssistant, cloud: None) -> None:
    coordinator = (await _setup(hass)).runtime_data

    new_id = await coordinator.async_add_schedule(
        schedule_type=SCHEDULE_CHARGE, power=2500, start=hm(2), end=hm(5)
    )

    assert new_id == 13
    assert _client().commands == [
        (
            "add",
            listed(13, SCHEDULE_CHARGE, 2500, hm(2), hm(5), unknown_2=None),
        )
    ]
    assert coordinator.schedules[13].power == 2500


async def test_add_with_chosen_days_and_switched_off(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass)).runtime_data

    await coordinator.async_add_schedule(
        schedule_type=SCHEDULE_USE_BATTERY,
        power=3000,
        start=hm(23),
        end=hm(2),
        days=1 + 2 + 4 + 8 + 16,
        enabled=False,
    )

    ((name, sent),) = _client().commands
    assert name == "add"
    assert (sent.enabled, sent.repeat, sent.days) == (False, 2, 31)
    assert (sent.start, sent.end) == (hm(23), hm(2))


async def test_two_adds_at_once_get_different_ids(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass)).runtime_data

    ids = await asyncio.gather(
        *(
            coordinator.async_add_schedule(
                schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(1), end=hm(2)
            )
            for _ in range(2)
        )
    )

    assert sorted(ids) == [13, 14]
    assert sorted(coordinator.schedules) == [10, 11, 12, 13, 14]


async def test_add_is_refused_before_the_list_is_known(
    hass: HomeAssistant, cloud: None
) -> None:
    ScheduleMqtt.answer_lists = False
    coordinator = (await _setup(hass)).runtime_data

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(1), end=hm(2)
        )

    assert _key(error) == "schedules_unknown"
    assert _client().commands == []


@pytest.mark.parametrize(
    ("changes", "key"),
    [
        ({"power": 6001}, "schedule_power_too_high"),
        ({"power": 0}, "schedule_invalid_power"),
        ({"start": hm(1, 15)}, "schedule_invalid_time"),
        ({"end": hm(1)}, "schedule_invalid_time"),
        ({"schedule_type": 3}, "schedule_invalid_type"),
        ({"days": 128}, "schedule_invalid_days"),
    ],
)
async def test_add_is_refused_for_invalid_values(
    hass: HomeAssistant, cloud: None, changes: dict[str, int], key: str
) -> None:
    coordinator = (await _setup(hass)).runtime_data
    values = {
        "schedule_type": SCHEDULE_CHARGE,
        "power": 1000,
        "start": hm(1),
        "end": hm(2),
    }

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(**(values | changes))

    assert _key(error) == key
    assert _client().commands == []


async def test_add_and_update_are_refused_without_the_max_power_option(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass, max_power=None)).runtime_data

    with pytest.raises(HomeAssistantError) as add_error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(1), end=hm(2)
        )
    with pytest.raises(HomeAssistantError) as update_error:
        await coordinator.async_update_schedule(12, end=hm(9))

    assert _key(add_error) == _key(update_error) == "max_schedule_power_not_set"
    assert _client().commands == []


# -----------------------------
# Updating
# -----------------------------


async def test_update_sends_the_whole_schedule_and_keeps_the_rest(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass)).runtime_data

    await coordinator.async_update_schedule(11, power=2500)

    # Repeat 65 of the older schedule is sent back unchanged
    assert _client().commands == [
        (
            "edit",
            listed(11, SCHEDULE_USE_BATTERY, 2500, hm(23), hm(2), repeat=65),
        )
    ]
    assert coordinator.schedules[11].power == 2500


async def test_update_time_type_and_enabled(hass: HomeAssistant, cloud: None) -> None:
    coordinator = (await _setup(hass)).runtime_data

    await coordinator.async_update_schedule(
        12,
        schedule_type=SCHEDULE_USE_BATTERY,
        start=hm(9),
        end=hm(11, 30),
        enabled=True,
    )

    ((_, sent),) = _client().commands
    assert (sent.schedule_type, sent.start, sent.end, sent.enabled) == (
        SCHEDULE_USE_BATTERY,
        hm(9),
        hm(11, 30),
        True,
    )
    assert (sent.power, sent.repeat, sent.days) == (1500, 1, 0)


@pytest.mark.parametrize(
    ("days", "expected"), [(96, (2, 96)), (127, (2, 127)), (0, (1, 0))]
)
async def test_update_days(
    hass: HomeAssistant, cloud: None, days: int, expected: tuple[int, int]
) -> None:
    coordinator = (await _setup(hass)).runtime_data

    await coordinator.async_update_schedule(12, days=days)

    ((_, sent),) = _client().commands
    assert (sent.repeat, sent.days) == expected


async def test_update_only_checks_the_power_when_it_changes(
    hass: HomeAssistant, cloud: None
) -> None:
    # The option was lowered below the 5000 W of schedule 10
    coordinator = (await _setup(hass, max_power=3000)).runtime_data

    await coordinator.async_update_schedule(10, end=hm(10))
    assert coordinator.schedules[10].end == hm(10)

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_update_schedule(10, power=3500)
    assert _key(error) == "schedule_power_too_high"


async def test_update_is_refused_for_invalid_values(
    hass: HomeAssistant, cloud: None
) -> None:
    coordinator = (await _setup(hass)).runtime_data

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_update_schedule(12, start=hm(6, 10))

    assert _key(error) == "schedule_invalid_time"
    assert _client().commands == []

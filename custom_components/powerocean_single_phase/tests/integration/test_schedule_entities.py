"""test_schedule_entities: the schedule switches, the active sensor and the actions."""

# ruff: noqa: F811 (an imported fixture is used as a test argument)

from dataclasses import replace
from typing import Any

import pytest
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.powerocean_single_phase.const import DOMAIN
from custom_components.powerocean_single_phase.mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_USE_BATTERY,
)
from custom_components.powerocean_single_phase.schedules import schedule_attributes
from custom_components.powerocean_single_phase.tests.integration.test_mqtt_schedules import (  # noqa: F401, E501
    ScheduleMqtt,
    _client,
    _setup,
    _tick,
    cloud,
    enable_custom_integrations_fixture,
    hm,
    listed,
    system_schedules,
)
from custom_components.powerocean_single_phase.tests.utils import SN, get_device

BATTERY_SN = "SN_BATTERIEPACK01"


def _entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


def _switch(hass: HomeAssistant, schedule_id: int) -> str:
    entity_id = _entity_id(hass, "switch", f"{SN}_schedule_{schedule_id}")
    assert entity_id, f"no switch for schedule {schedule_id}"
    return entity_id


def _schedule_switches(hass: HomeAssistant) -> list[str]:
    prefix = f"{SN}_schedule_"
    return [
        entry.unique_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN and entry.unique_id.startswith(prefix)
    ]


async def _call(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    await hass.async_block_till_done()


# -----------------------------
# The switches
# -----------------------------


async def test_a_switch_for_each_schedule(hass: HomeAssistant, cloud: None) -> None:
    await _setup(hass)

    assert sorted(_schedule_switches(hass)) == [
        f"{SN}_schedule_{n}" for n in (10, 11, 12)
    ]
    on, off = hass.states.get(_switch(hass, 10)), hass.states.get(_switch(hass, 12))
    assert (on.state, off.state) == ("on", "off")
    # The name says what the schedule does
    assert on.name.endswith("Use battery 08:30-09:00 5000 W")
    assert off.name.endswith("Charge 06:00-08:30 1500 W")
    expected = schedule_attributes(system_schedules()[0])
    assert {key: on.attributes[key] for key in expected} == expected


async def test_the_entity_id_stays_when_the_schedule_changes(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)
    entity_id = _switch(hass, 12)
    assert entity_id.endswith("schedule_12")

    await _call(
        hass,
        "update_schedule",
        entity_id=entity_id,
        start="09:00:00",
        end="11:30:00",
        power=3000,
    )

    assert _switch(hass, 12) == entity_id
    state = hass.states.get(entity_id)
    assert state.name.endswith("Charge 09:00-11:30 3000 W")
    assert state.attributes["start"] == "09:00"


async def test_no_schedule_entities_without_the_mqtt_option(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass, mqtt=False)

    assert _schedule_switches(hass) == []
    assert _entity_id(hass, "sensor", f"{SN}_active_schedule") is None


async def test_the_entities_go_when_the_mqtt_option_is_switched_off(
    hass: HomeAssistant, cloud: None
) -> None:
    entry = await _setup(hass)
    assert _schedule_switches(hass)
    assert _entity_id(hass, "sensor", f"{SN}_active_schedule")

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "mqtt": False}
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _schedule_switches(hass) == []
    assert _entity_id(hass, "sensor", f"{SN}_active_schedule") is None


async def test_switch_on_and_off(hass: HomeAssistant, cloud: None) -> None:
    await _setup(hass)
    entity_id = _switch(hass, 12)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert _client().commands == [("switch", (12, 1))]
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert _client().commands[-1] == ("switch", (12, 0))
    assert hass.states.get(entity_id).state == "off"


async def test_switches_follow_the_list(
    hass: HomeAssistant, cloud: None, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass)
    removed = _switch(hass, 11)
    # The app adds the schedule 13 and deletes 11
    ScheduleMqtt.system = [
        *(s for s in system_schedules() if s.schedule_id != 11),
        listed(13, SCHEDULE_CHARGE, 1000, hm(1), hm(2)),
    ]

    await _tick(hass, freezer, 60)

    assert sorted(_schedule_switches(hass)) == [
        f"{SN}_schedule_{n}" for n in (10, 12, 13)
    ]
    assert hass.states.get(removed) is None
    assert hass.states.get(_switch(hass, 13)).state == "on"


async def test_a_switch_of_an_earlier_run_that_is_gone_is_removed(
    hass: HomeAssistant, cloud: None, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch", DOMAIN, f"{SN}_schedule_99", config_entry=entry
    )

    await _tick(hass, freezer, 60)

    assert f"{SN}_schedule_99" not in _schedule_switches(hass)


async def test_the_switches_are_unavailable_without_the_connection(
    hass: HomeAssistant, cloud: None
) -> None:
    entry = await _setup(hass)
    entity_id = _switch(hass, 12)
    entry.runtime_data.mqtt = None
    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "unavailable"


# -----------------------------
# The active schedule
# -----------------------------


def _active(hass: HomeAssistant) -> Any:
    entity_id = _entity_id(hass, "sensor", f"{SN}_active_schedule")
    assert entity_id
    assert entity_id.endswith("active_schedule")
    return hass.states.get(entity_id)


async def test_active_schedule_sensor(
    hass: HomeAssistant, cloud: None, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass)

    state = _active(hass)
    assert state.state == "Use battery 08:30-09:00 5000 W"
    assert state.attributes["id"] == 10
    assert state.attributes["in_use"] is True
    # 08:30:22: the system moves on by itself
    ScheduleMqtt.system = [
        replace(s, in_use=s.schedule_id == 11) for s in system_schedules()
    ]

    await _tick(hass, freezer, 60)

    state = _active(hass)
    assert state.state == "Use battery 23:00-02:00 2000 W"
    assert state.attributes["id"] == 11


async def test_active_schedule_sensor_without_a_schedule_in_use(
    hass: HomeAssistant, cloud: None
) -> None:
    ScheduleMqtt.system = [replace(s, in_use=False) for s in system_schedules()]

    await _setup(hass)

    state = _active(hass)
    assert state.state == "none"
    assert "id" not in state.attributes


# -----------------------------
# The actions
# -----------------------------


async def test_add_schedule(hass: HomeAssistant, cloud: None) -> None:
    await _setup(hass)
    device = get_device(hass, SN)
    assert device

    await _call(
        hass,
        "add_schedule",
        device_id=device.id,
        type="charge",
        power=2500,
        start="02:00:00",
        end="05:30:00",
    )

    assert _client().commands == [
        ("add", listed(13, SCHEDULE_CHARGE, 2500, hm(2), hm(5, 30), unknown_2=None))
    ]
    assert hass.states.get(_switch(hass, 13)).state == "on"


async def test_add_schedule_with_days_switched_off(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)
    device = get_device(hass, SN)
    assert device

    await _call(
        hass,
        "add_schedule",
        device_id=device.id,
        type="use_battery",
        power=3000,
        start="10:00:00",
        end="11:00:00",
        days=["mon", "tue", "wed", "thu", "fri"],
        enabled=False,
    )

    ((name, sent),) = _client().commands
    assert name == "add"
    assert sent.schedule_type == SCHEDULE_USE_BATTERY
    assert (sent.enabled, sent.repeat, sent.days) == (False, 2, 31)
    assert hass.states.get(_switch(hass, 13)).state == "off"


@pytest.mark.parametrize(
    ("change", "key"),
    [
        ({"start": "02:00:30"}, "schedule_invalid_time"),
        ({"start": "02:15:00"}, "schedule_invalid_time"),
        ({"power": 6001}, "schedule_power_too_high"),
        ({"power": 0}, "schedule_invalid_power"),
        ({"power": 1000.5}, "schedule_invalid_power"),
    ],
)
async def test_add_schedule_refuses_invalid_input(
    hass: HomeAssistant, cloud: None, change: dict[str, Any], key: str
) -> None:
    await _setup(hass)
    device = get_device(hass, SN)
    assert device
    data = {
        "device_id": device.id,
        "type": "charge",
        "power": 1000,
        "start": "02:00:00",
        "end": "05:00:00",
    }

    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, "add_schedule", **(data | change))

    assert error.value.translation_key == key
    assert _client().commands == []


async def test_add_schedule_needs_the_max_power_option(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass, max_power=None)
    device = get_device(hass, SN)
    assert device

    with pytest.raises(HomeAssistantError) as error:
        await _call(
            hass,
            "add_schedule",
            device_id=device.id,
            type="charge",
            power=1000,
            start="02:00:00",
            end="05:00:00",
        )

    assert error.value.translation_key == "max_schedule_power_not_set"


async def test_add_schedule_unknown_type_is_refused(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)
    device = get_device(hass, SN)
    assert device

    with pytest.raises(vol.Invalid):
        await _call(
            hass,
            "add_schedule",
            device_id=device.id,
            type="export",
            power=1000,
            start="02:00:00",
            end="05:00:00",
        )


@pytest.mark.parametrize("target", ["battery", "unknown"])
async def test_add_schedule_needs_the_inverter(
    hass: HomeAssistant, cloud: None, target: str
) -> None:
    await _setup(hass)
    device = get_device(hass, BATTERY_SN)
    assert device
    device_id = device.id if target == "battery" else "0123456789abcdef"

    with pytest.raises(HomeAssistantError) as error:
        await _call(
            hass,
            "add_schedule",
            device_id=device_id,
            type="charge",
            power=1000,
            start="02:00:00",
            end="05:00:00",
        )

    assert error.value.translation_key == "schedule_target"
    assert _client().commands == []


async def test_update_schedule_keeps_what_is_not_given(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)

    await _call(hass, "update_schedule", entity_id=_switch(hass, 11), power=2500)

    ((name, sent),) = _client().commands
    assert name == "edit"
    assert (sent.power, sent.repeat, sent.days) == (2500, 65, 0)
    assert (sent.start, sent.end) == (hm(23), hm(2))


async def test_update_schedule_days_and_daily(hass: HomeAssistant, cloud: None) -> None:
    await _setup(hass)
    entity_id = _switch(hass, 12)

    await _call(hass, "update_schedule", entity_id=entity_id, days=["mon", "tue"])
    assert (_client().commands[-1][1].repeat, _client().commands[-1][1].days) == (2, 3)
    assert hass.states.get(entity_id).attributes["days"] == ["mon", "tue"]

    await _call(hass, "update_schedule", entity_id=entity_id, daily=True)
    assert (_client().commands[-1][1].repeat, _client().commands[-1][1].days) == (1, 0)
    assert hass.states.get(entity_id).attributes["days"] == "daily"


async def test_update_schedule_days_and_daily_together_are_refused(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)

    with pytest.raises(HomeAssistantError) as error:
        await _call(
            hass,
            "update_schedule",
            entity_id=_switch(hass, 12),
            days=["mon"],
            daily=True,
        )

    assert error.value.translation_key == "schedule_days_and_daily"
    assert _client().commands == []


async def test_update_schedule_checks_the_values(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)

    with pytest.raises(HomeAssistantError) as error:
        await _call(hass, "update_schedule", entity_id=_switch(hass, 12), power=7000)

    assert error.value.translation_key == "schedule_power_too_high"


async def test_delete_schedule(hass: HomeAssistant, cloud: None) -> None:
    await _setup(hass)
    entity_id = _switch(hass, 11)

    await _call(hass, "delete_schedule", entity_id=entity_id)

    assert _client().commands == [("delete", 11)]
    assert hass.states.get(entity_id) is None
    assert f"{SN}_schedule_11" not in _schedule_switches(hass)


async def test_home_assistant_describes_the_actions(
    hass: HomeAssistant, cloud: None
) -> None:
    from homeassistant.helpers.service import (  # noqa: PLC0415
        async_get_all_descriptions,
    )
    from homeassistant.helpers.translation import (  # noqa: PLC0415
        async_get_translations,
    )

    await _setup(hass)

    actions = (await async_get_all_descriptions(hass))[DOMAIN]
    texts = await async_get_translations(hass, "en", "services", {DOMAIN})

    assert set(actions) == {"add_schedule", "update_schedule", "delete_schedule"}
    add = actions["add_schedule"]
    assert {name for name, field in add["fields"].items() if field.get("required")} == {
        "type",
        "power",
        "start",
        "end",
    }
    assert add["fields"]["enabled"]["default"] is True
    assert add["target"] == {"device": [{"integration": DOMAIN}]}
    assert actions["update_schedule"]["target"]["entity"] == [
        {"integration": DOMAIN, "domain": ["switch"]}
    ]
    assert "daily" in actions["update_schedule"]["fields"]
    prefix = f"component.{DOMAIN}.services"
    assert texts[f"{prefix}.add_schedule.name"] == "Add schedule"
    assert texts[f"{prefix}.add_schedule.fields.power.name"] == "Power"
    assert texts[f"{prefix}.delete_schedule.name"] == "Delete schedule"


async def test_add_schedule_refuses_an_overlap(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)
    device = get_device(hass, SN)
    assert device

    with pytest.raises(HomeAssistantError) as error:
        await _call(
            hass,
            "add_schedule",
            device_id=device.id,
            type="charge",
            power=1000,
            start="08:00:00",
            end="10:00:00",
        )

    assert error.value.translation_key == "schedule_overlap"
    assert _client().commands == []


async def test_the_active_sensor_entity_id_has_no_serial_number(
    hass: HomeAssistant, cloud: None
) -> None:
    await _setup(hass)

    entity_id = _entity_id(hass, "sensor", f"{SN}_active_schedule")

    assert entity_id
    assert SN.lower() not in entity_id

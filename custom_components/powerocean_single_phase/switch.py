"""
PowerOcean switches: settings sent to the system via MQTT.

Only with the option "Extended connection (MQTT)": the battery save mode, and
one switch for each scheduled task of the app ("Scheduled tasks"). The
switches of the schedules follow the list the system reports; their name
says what the schedule does and changes with it, the entity ID does not.
"""

from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MQTT, DOMAIN
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .mqtt import Schedule
from .schedules import schedule_attributes, schedule_title
from .services import (
    SERVICE_DELETE_SCHEDULE,
    SERVICE_UPDATE_SCHEDULE,
    UPDATE_SCHEMA,
    changes_from_service,
)

# One command at a time, each waits for its acknowledgement
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches, if the MQTT option is on."""
    coordinator = entry.runtime_data
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_UPDATE_SCHEDULE, UPDATE_SCHEMA, "async_update_schedule"
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_SCHEDULE, None, "async_delete_schedule"
    )
    entity_registry = er.async_get(hass)
    prefix = f"{coordinator.api.sn}_schedule_"

    def registered_schedules() -> dict[int, str]:
        """Return the schedule switches in the registry: id -> entity ID."""
        return {
            int(registry_entry.unique_id.removeprefix(prefix)): registry_entry.entity_id
            for registry_entry in er.async_entries_for_config_entry(
                entity_registry, entry.entry_id
            )
            if registry_entry.domain == SWITCH_DOMAIN
            and registry_entry.unique_id.startswith(prefix)
        }

    switches = [PowerOceanBatterySaveMode(coordinator)]
    if not entry.options.get(CONF_MQTT, False):
        # Switched off: remove the switches instead of leaving them unavailable
        for switch in switches:
            if entity_id := entity_registry.async_get_entity_id(
                SWITCH_DOMAIN, DOMAIN, switch.unique_id
            ):
                entity_registry.async_remove(entity_id)
        for entity_id in registered_schedules().values():
            entity_registry.async_remove(entity_id)
        return
    async_add_entities(switches)

    added: set[int] = set()

    @callback
    def _follow_the_schedules() -> None:
        """Add a switch for each schedule, remove the ones that are gone."""
        if not coordinator.schedules_known:
            return
        ids = set(coordinator.schedules)
        if new := sorted(ids - added):
            async_add_entities(
                [PowerOceanSchedule(coordinator, schedule_id) for schedule_id in new]
            )
        # Also the switches of an earlier run that are not in the list any more
        registered = registered_schedules()
        for schedule_id in (added | set(registered)) - ids:
            if entity_id := registered.get(schedule_id):
                entity_registry.async_remove(entity_id)
        added.clear()
        added.update(ids)

    _follow_the_schedules()
    entry.async_on_unload(coordinator.async_add_listener(_follow_the_schedules))


class PowerOceanBatterySaveMode(CoordinatorEntity[PowerOceanCoordinator], SwitchEntity):
    """Battery save mode of the app, only reported via MQTT."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "battery_save_mode"

    def __init__(self, coordinator: PowerOceanCoordinator) -> None:
        """Initialize the switch of the inverter."""
        super().__init__(coordinator)
        sn = coordinator.api.sn
        self._attr_unique_id = f"{sn}_{self._attr_translation_key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def available(self) -> bool:
        """Return False without MQTT connection, state or sysZone (sent along)."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.is_on is not None
            and self.coordinator.setting("sysZone") is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the state the system reported last."""
        value = self.coordinator.setting("batterySaveMode")
        return None if value is None else bool(value)

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Switch on; raises if the system does not confirm it."""
        await self.coordinator.async_set_battery_save_mode(enabled=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Switch off; raises if the system does not confirm it."""
        await self.coordinator.async_set_battery_save_mode(enabled=False)


class PowerOceanSchedule(CoordinatorEntity[PowerOceanCoordinator], SwitchEntity):
    """
    A scheduled task of the app: on or off.

    The unique_id has the id of the schedule, so the entity ID stays fixed
    while the name follows the schedule.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PowerOceanCoordinator, schedule_id: int) -> None:
        """Initialize the switch of a schedule of the inverter."""
        super().__init__(coordinator)
        self._schedule_id = schedule_id
        sn = coordinator.api.sn
        self._attr_unique_id = f"{sn}_schedule_{schedule_id}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def suggested_object_id(self) -> str:
        """Return the id of the schedule, so the entity ID does not depend on it."""
        return f"schedule_{self._schedule_id}"

    @property
    def _schedule(self) -> Schedule | None:
        return self.coordinator.schedules.get(self._schedule_id)

    @property
    def name(self) -> str:
        """Return what the schedule does, e.g. "Charge 02:00-06:00 3000 W"."""
        schedule = self._schedule
        return schedule_title(schedule) if schedule else f"Schedule {self._schedule_id}"

    @property
    def available(self) -> bool:
        """Return False without MQTT connection or if the schedule is gone."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self._schedule is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the schedule is switched on."""
        schedule = self._schedule
        return None if schedule is None else schedule.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return id, type, start, end, power, days and whether it is in use."""
        schedule = self._schedule
        return schedule_attributes(schedule) if schedule else {}

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Switch on; raises if the system does not confirm it."""
        await self.coordinator.async_set_schedule_enabled(
            self._schedule_id, enabled=True
        )

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Switch off; raises if the system does not confirm it."""
        await self.coordinator.async_set_schedule_enabled(
            self._schedule_id, enabled=False
        )

    async def async_update_schedule(self, **service_data: Any) -> None:
        """Change the schedule (action update_schedule); what is not given stays."""
        await self.coordinator.async_update_schedule(
            self._schedule_id, **changes_from_service(service_data)
        )

    async def async_delete_schedule(self) -> None:
        """Delete the schedule (action delete_schedule)."""
        await self.coordinator.async_delete_schedule(self._schedule_id)

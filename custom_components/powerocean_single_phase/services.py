"""
PowerOcean actions: add, update and delete a scheduled task.

Add targets the inverter device, as there may be no schedule yet to point at.
Update and delete target a schedule switch (registered with the switch
platform). All three need the option "Max schedule power (W)", except delete.
"""

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import PowerOceanCoordinator
from .schedules import DAY_BITS, TYPE_BY_KEY, ScheduleInputError, schedule_changes

SERVICE_ADD_SCHEDULE = "add_schedule"
SERVICE_UPDATE_SCHEDULE = "update_schedule"
SERVICE_DELETE_SCHEDULE = "delete_schedule"

# The power is checked later: a number, whole watts, from 1 to the option
_POWER = vol.Coerce(float)
_TYPE = vol.In(list(TYPE_BY_KEY))
_DAYS = vol.All(cv.ensure_list, [vol.In(list(DAY_BITS))])

ADD_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Required("type"): _TYPE,
        vol.Required("power"): _POWER,
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Optional("days"): _DAYS,
        vol.Optional("enabled", default=True): cv.boolean,
    }
)

# For the entity service: the schema of the target is added by Home Assistant
UPDATE_SCHEMA: dict[Any, Any] = {
    vol.Optional("type"): _TYPE,
    vol.Optional("power"): _POWER,
    vol.Optional("start"): cv.time,
    vol.Optional("end"): cv.time,
    vol.Optional("days"): _DAYS,
    vol.Optional("daily"): cv.boolean,
    vol.Optional("enabled"): cv.boolean,
}


def changes_from_service(data: dict[str, Any]) -> dict[str, Any]:
    """Return the arguments of the coordinator; wrong input is a service error."""
    try:
        return schedule_changes(data)
    except ScheduleInputError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key=err.key
        ) from err


def _coordinator_for_device(
    hass: HomeAssistant, device_id: str
) -> PowerOceanCoordinator:
    """Return the coordinator of the system whose inverter is the device."""
    device = dr.async_get(hass).async_get(device_id)
    for entry_id in device.config_entries if device else ():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            coordinator: PowerOceanCoordinator = entry.runtime_data
            if device and (DOMAIN, coordinator.api.sn) in device.identifiers:
                return coordinator
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="schedule_target"
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the action that targets a device (the others are entity services)."""

    async def add_schedule(call: ServiceCall) -> None:
        changes = changes_from_service(call.data)
        for device_id in call.data["device_id"]:
            await _coordinator_for_device(hass, device_id).async_add_schedule(**changes)

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_SCHEDULE, add_schedule, schema=ADD_SCHEMA
    )

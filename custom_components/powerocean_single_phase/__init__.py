"""
PowerOcean integration for Home Assistant.

This module sets up the PowerOcean integration, handling:

- Authentication and device setup via EcoFlow API.
- Migration of old config entries to new structure.
- Creation of a DataUpdateCoordinator for periodic polling.
- Registration in the Home Assistant device registry.
- Forwarding entry setups to sensor/platform components.

Functions:
- async_setup: Register the actions (scheduled tasks).
- async_setup_entry: Set up a PowerOcean device from a config entry.
- async_migrate_entry: Migrate older config entries to the latest version.
- async_remove_config_entry_device: Allow removing devices that are gone.
- async_unload_entry: Unload a config entry and clean up resources.
"""

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRIENDLY_NAME,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_MQTT,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MODEL_NAME_MAP,
    PLATFORMS,
    PORTAL_URL,
    PowerOceanModel,
)
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .datapoints import EDEV_SYSTEM_KEYS, SN_KEYS
from .ecoflow import HAEcoflowApi
from .services import async_setup_services

if TYPE_CHECKING:
    from collections.abc import Callable


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register the actions of the integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PowerOceanConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    device_id = entry.data.get(CONF_DEVICE_ID)
    model_id = entry.data.get(CONF_MODEL_ID)
    if not device_id or not model_id:
        # Cannot fix itself, so no retry
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="missing_configuration"
        )

    api = HAEcoflowApi(
        hass,
        device_id,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        model_id,
    )

    # Login and first fetch. Network errors retry the setup, rejected
    # credentials start the reauth flow.
    coordinator = PowerOceanCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # --- Sensor-Plattformen laden ---
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    model_name = MODEL_NAME_MAP[PowerOceanModel(model_id)]
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_id)},
        manufacturer="EcoFlow",
        serial_number=device_id,
        name=entry.options.get(CONF_FRIENDLY_NAME),
        model=model_name,
        model_id=model_id,
        configuration_url=PORTAL_URL,
    )

    # Optional: firmware of the modules via MQTT. Failures do not stop the
    # setup, the values come from HTTP.
    if entry.options.get(CONF_MQTT, False):
        entry.async_on_unload(coordinator.async_stop_mqtt)
        await coordinator.async_start_mqtt()
    else:
        # Switched off: no outdated firmware on the device pages
        coordinator.clear_mqtt_firmware()

    return True


async def async_migrate_entry(
    hass: HomeAssistant, entry: PowerOceanConfigEntry
) -> bool:
    """Migrate old config entries to new structure."""
    if entry.version > 2:
        # Downgrading from a newer version is not supported
        return False

    data = dict(entry.data)
    options = dict(entry.options)

    if entry.version < 2 and "user_input" in data:
        # 1.x: credentials under data["user_input"], name and interval
        # under data["options"] (see below)
        old = data.pop("user_input")

        data.update(
            {
                CONF_DEVICE_ID: old.get(CONF_DEVICE_ID),
                CONF_EMAIL: old.get(CONF_EMAIL),
                CONF_PASSWORD: old.get(CONF_PASSWORD),
                CONF_MODEL_ID: old.get(CONF_MODEL_ID),
            }
        )

    if entry.version < 2 or entry.minor_version < 2:
        # 1.x and 2.1: take the options from data["options"] and remove
        # the legacy values from data
        legacy_options = data.pop("options", None) or {}
        data.pop("device_info", None)
        legacy_name = legacy_options.get(CONF_FRIENDLY_NAME)

        options.setdefault(
            CONF_SCAN_INTERVAL,
            legacy_options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        # The migration to 2.1 missed the name in data["options"] and set
        # the default name. Leave a custom name alone.
        if legacy_name and options.get(CONF_FRIENDLY_NAME) in (None, DEFAULT_NAME):
            options[CONF_FRIENDLY_NAME] = legacy_name
        options.setdefault(CONF_FRIENDLY_NAME, DEFAULT_NAME)

    # Entities that are no longer provided, by the version that dropped them
    inverter = data.get(CONF_DEVICE_ID) or ""
    obsolete: list[tuple[int, Callable[[str], bool]]] = [
        # 2.3: The battery firmware is now shown on the device page
        (3, lambda unique_id: unique_id.endswith("_moduleAplSwVer")),
        # 2.4: The system values from EDEV_SYS_REPORT belonged to the
        # wallbox, now to the inverter (new unique_id)
        (4, lambda unique_id: _is_edev_system_value_of_device(unique_id, inverter)),
        # 2.5: The serial numbers are already in the device info
        (5, lambda unique_id: unique_id.rpartition("_")[2] in SN_KEYS),
    ]
    removed = [
        is_obsolete
        for minor, is_obsolete in obsolete
        if entry.version < 2 or entry.minor_version < minor
    ]
    if removed:
        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if any(is_obsolete(entity.unique_id) for is_obsolete in removed):
                entity_registry.async_remove(entity.entity_id)

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=2,
        minor_version=5,
    )
    LOGGER.debug(
        "Migrated %s to version %s.%s", entry.title, entry.version, entry.minor_version
    )
    return True


def _is_edev_system_value_of_device(unique_id: str, inverter: str) -> bool:
    """Return True for a system value of EDEV_SYS_REPORT on another device."""
    # unique_id = <serial number>_<prefix>_EDEV_SYS_REPORT_<key>
    head, found, key = unique_id.rpartition("_EDEV_SYS_REPORT_")
    return bool(found) and key in EDEV_SYSTEM_KEYS and not head.startswith(inverter)


async def async_unload_entry(hass: HomeAssistant, entry: PowerOceanConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing a device that has no values in the last response."""
    if entry.state is not ConfigEntryState.LOADED:
        # Without a response it is unknown which devices are present
        return False
    coordinator = entry.runtime_data
    present = {
        coordinator.endpoints[unique_id].serial
        for unique_id in coordinator.data
        if unique_id in coordinator.endpoints
    }
    serials = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    }
    if serials & present:
        return False
    # Its entities are added again if the device returns
    for unique_id, endpoint in list(coordinator.endpoints.items()):
        if endpoint.serial in serials:
            del coordinator.endpoints[unique_id]
    return True

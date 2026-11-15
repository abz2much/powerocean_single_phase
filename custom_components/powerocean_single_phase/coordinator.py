"""
coordinator.py: Home Assistant DataUpdateCoordinator for PowerOcean.

This module defines `PowerOceanCoordinator`, a subclass of
`DataUpdateCoordinator` that periodically fetches live data from
EcoFlow devices using `HAEcoflowApi` and parses them with `parse()`.

It handles:
- Login during the first refresh (`_async_setup`)
- Automatic periodic updates
- A new login when the EcoFlow cloud rejects the current token
- Mapping API errors to `UpdateFailed` / `ConfigEntryAuthFailed`
- Building the endpoints, extended by datapoints that appear later
- Optional MQTT connection: firmware, settings and live power values

Usage:
  coordinator = PowerOceanCoordinator(hass, entry, api)
  await coordinator.async_config_entry_first_refresh()
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, NoReturn

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AuthenticationError, EcoflowApiError, EcoflowConnectionError
from .const import (
    CONF_LIVE_POWER,
    CONF_MAX_SCHEDULE_POWER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
)
from .ecoflow import HAEcoflowApi
from .flows import energy_flows
from .mqtt import (
    ENERGY_STREAM_STRINGS,
    MODULE_EMS,
    MODULE_METER,
    SCHEDULE_ADD,
    SCHEDULE_CHARGE,
    SCHEDULE_EDIT,
    SCHEDULE_REPEAT_DAILY,
    SCHEDULE_REPEAT_DAYS,
    SCHEDULE_USE_BATTERY,
    ModuleVersion,
    MqttCallbacks,
    PowerOceanMqtt,
    Schedule,
    new_seq,
)
from .parse import parse
from .schedules import schedule_title, windows_overlap
from .types import PowerOceanEndPoint

if TYPE_CHECKING:
    from .errors import ActiveError

type PowerOceanConfigEntry = ConfigEntry[PowerOceanCoordinator]

# How often the module firmware is asked for again (it also comes after
# every reconnect, e.g. after a firmware update)
MQTT_REFRESH_INTERVAL = timedelta(hours=6)
# How often the energy stream is asked for (option live power); the system
# sends it for ~25 s after each request
ENERGY_STREAM_INTERVAL = timedelta(seconds=20)
# How long the power values of the energy stream win over the HTTP response,
# which shows older values
ENERGY_STREAM_HOLD = timedelta(seconds=30)
# How long a command may wait for the acknowledgement of the system
COMMAND_TIMEOUT = 10
# How often the schedules are asked for (the system does not report changes,
# e.g. when it moves to the next schedule by itself)
SCHEDULE_POLL_INTERVAL = timedelta(seconds=60)
# Seconds between two readings while waiting for the list to show a change
SCHEDULE_CONFIRM_DELAY = 0.5
SCHEDULE_TYPES = (SCHEDULE_CHARGE, SCHEDULE_USE_BATTERY)
# The most schedules the app allows
MAX_SCHEDULES = 6
# The app sets times in steps of 30 minutes, from 00:00 to 23:30
SCHEDULE_STEP = 30
SCHEDULE_LAST_MINUTE = 23 * 60 + 30
# All seven days as a bit mask (Monday 1 to Sunday 64)
ALL_DAYS = 127
# Sent together with the backup reserve: datapoint -> report it is read from.
# Not from the values directly under "data": those can be 0 while the
# report holds the setting.
SOC_LIMITS = {
    "sysBatChgUpLimit": "_EMS_CHANGE_REPORT",
    "sysBatDsgDownLimit": "_EMS_CHANGE_REPORT",
    "devSoc": "_EMS_PARAM_CHANGE_REPORT",
}
# Settings only known from the MQTT reports (EMS parameter change report)
MQTT_SETTINGS = ("batterySaveMode", "sysZone")
# How long a setting the system confirmed via MQTT wins over the HTTP
# response: the cloud shows a change only 20-60 s later
SETTING_HOLD = timedelta(minutes=2)

# Parser errors caused by unexpected API payloads
PARSE_ERRORS = (KeyError, IndexError, TypeError, ValueError, AttributeError)


class PowerOceanCoordinator(DataUpdateCoordinator[dict[str, float | int | str]]):
    """Coordinate periodic fetching and parsing of PowerOcean sensor values."""

    config_entry: PowerOceanConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: PowerOceanConfigEntry,
        api: HAEcoflowApi,
    ) -> None:
        """Initialize the PowerOcean data update coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name="PowerOcean",
            update_interval=timedelta(
                seconds=config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                )
            ),
        )
        self.api = api
        # Grows with every new datapoint, read by the platforms
        self.endpoints: dict[str, PowerOceanEndPoint] = {}
        # Last raw API response, for diagnostics
        self.last_response: dict[str, Any] | None = None
        # Active errors, by unique_id of the error count sensor
        self.active_errors: dict[str, list[ActiveError]] = {}
        # Firmware version, by serial number of the device
        self.firmware: dict[str, str] = {}
        self._http_firmware: dict[str, str] = {}
        # Optional MQTT connection (option "mqtt"): firmware of the modules
        self.mqtt: PowerOceanMqtt | None = None
        self.modules: dict[int, ModuleVersion] = {}
        self._mqtt_firmware: dict[str, str] = {}
        self._mqtt_refresh_unsub: Callable[[], None] | None = None
        # Charge limit, backup reserve and devSoc (see SOC_LIMITS)
        self.soc_limits: dict[str, int] = {}
        # Battery save mode and sysZone (see MQTT_SETTINGS)
        self.mqtt_settings: dict[str, int] = {}
        # Settings confirmed via MQTT the HTTP response may not show yet:
        # name -> (value, time of the confirmation)
        self._recent_settings: dict[str, tuple[int, datetime]] = {}
        # Option live power: values of the last energy stream report, by
        # unique_id, and when it came
        self._energy_stream_unsub: Callable[[], None] | None = None
        self._stream_values: dict[str, float | int] = {}
        self._stream_time: datetime | None = None
        # Commands waiting for their acknowledgement, by seq
        self._pending_acks: dict[int, asyncio.Future[None]] = {}
        # Scheduled tasks (option MQTT), by id; None-known until the first list
        self.schedules: dict[int, Schedule] = {}
        self._schedules_known = False
        # Highest id of a schedule we added: the list may not show it yet
        self._last_added_id = 0
        self._schedule_poll_unsub: Callable[[], None] | None = None
        # Waiting for the list after a command
        self._schedule_waiters: list[asyncio.Future[None]] = []
        # One schedule command at a time: the next id is the highest plus 1
        self._schedule_lock = asyncio.Lock()

    async def _async_setup(self) -> None:
        """Log in once before the first refresh."""
        await self._async_authorize()

    async def _async_update_data(self) -> dict[str, float | int | str]:
        """Fetch and parse the live data, adding endpoints for new datapoints."""
        response = await self._async_fetch()
        self.last_response = response

        try:
            result = parse(response, self.api.sn, self.api.variant)
            # A device that was offline at startup brings its datapoints later
            for unique_id, datapoint in result.first().items():
                if unique_id not in self.endpoints:
                    self.endpoints[unique_id] = PowerOceanEndPoint.from_datapoint(
                        datapoint, result.firmware.get(datapoint.device.sn)
                    )
        except PARSE_ERRORS as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_response",
                translation_placeholders={"error": repr(err)},
            ) from err

        self.active_errors = result.active_errors
        self.soc_limits = soc_limits_of(response)
        self._update_firmware(result.firmware)
        values = result.values()
        self._hold_recent_settings(values)
        self._hold_energy_stream(values)
        return values

    def _hold_recent_settings(self, values: dict[str, float | int | str]) -> None:
        """
        Keep settings confirmed via MQTT the HTTP response does not show yet.

        Otherwise a slider jumps back after a change, and the next command
        for the SOC_LIMITS would send the old values along again.
        """
        now = dt_util.utcnow()
        for name, (value, since) in list(self._recent_settings.items()):
            keys = self.setting_keys(name, values)
            if name in SOC_LIMITS:
                reported = self.soc_limits.get(name)
            else:
                reported = values.get(keys[0]) if keys else None
            if reported == value or now - since > SETTING_HOLD:
                del self._recent_settings[name]
                continue
            if name in SOC_LIMITS:
                self.soc_limits[name] = value
            for key in keys:
                values[key] = value

    def _update_firmware(self, firmware: dict[str, str]) -> None:
        """Show a changed firmware version on the device page."""
        self._http_firmware = firmware
        self.firmware = {**firmware, **self._mqtt_firmware}
        # Devices not registered yet get the version from their entities
        device_registry = dr.async_get(self.hass)
        devices = self.devices(device_registry)
        for sn, version in self.firmware.items():
            device = devices.get(sn)
            if device and device.sw_version != version:
                device_registry.async_update_device(device.id, sw_version=version)

    def devices(self, device_registry: dr.DeviceRegistry) -> dict[str, dr.DeviceEntry]:
        """Return the devices of this config entry, by serial number."""
        # Not async_get_device: deprecated since Home Assistant 2026.10, and its
        # replacement async_get_device_by_identifier is missing before 2026.10
        return {
            sn: device
            for device in dr.async_entries_for_config_entry(
                device_registry, self.config_entry.entry_id
            )
            for domain, sn in device.identifiers
            if domain == DOMAIN
        }

    async def _async_fetch(self) -> dict[str, Any]:
        """Fetch data, logging in again once if the request was rejected."""
        try:
            return await self.api.fetch_raw()
        except EcoflowConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except EcoflowApiError as err:
            # Typically an expired token, reported as HTTP 401/403 or as an
            # error code in an HTTP 200 response.
            LOGGER.debug("Request rejected (%s), logging in again", err)

        await self._async_authorize()

        try:
            return await self.api.fetch_raw()
        except EcoflowApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _async_authorize(self) -> None:
        """Log in and map errors to the matching Home Assistant behavior."""
        try:
            await self.api.async_authorize()
        except AuthenticationError as err:
            # Starts the reauth flow
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except EcoflowApiError as err:
            # Retried with the next update (or setup retry during first refresh)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    # -----------------------------
    # Optional MQTT connection
    # -----------------------------

    async def async_start_mqtt(self) -> None:
        """Connect via MQTT to read the module firmware; failures only log."""
        if getattr(self.api, "mock_response", None) is not None:
            LOGGER.debug("Mocked response: no MQTT connection")
            return
        if not self.api.user_id:
            LOGGER.warning("MQTT: no user ID from the login, not connecting")
            return
        try:
            credentials = await self.api.async_mqtt_credentials()
            callbacks = MqttCallbacks(
                on_modules=self._from_mqtt_thread(self._apply_modules),
                on_settings=self._from_mqtt_thread(self._apply_settings),
                on_ack=self._from_mqtt_thread(self._apply_ack),
                on_energy_stream=self._from_mqtt_thread(self._apply_energy_stream),
                on_schedules=self._from_mqtt_thread(self._apply_schedules),
                on_schedule=self._from_mqtt_thread(self._apply_schedule),
            )
            client = PowerOceanMqtt(
                credentials, self.api.sn, self.api.user_id, callbacks
            )
            await self.hass.async_add_executor_job(client.start)
        except (EcoflowApiError, OSError, ValueError, KeyError) as err:
            LOGGER.warning("MQTT not started, no module firmware: %s", err)
            return
        self.mqtt = client
        self._mqtt_refresh_unsub = async_track_time_interval(
            self.hass, self._async_request_full_state, MQTT_REFRESH_INTERVAL
        )
        if self.config_entry.options.get(CONF_LIVE_POWER, False):
            self._energy_stream_unsub = async_track_time_interval(
                self.hass, self._async_request_energy_stream, ENERGY_STREAM_INTERVAL
            )
            await self._async_request_energy_stream(dt_util.utcnow())
        self._schedule_poll_unsub = async_track_time_interval(
            self.hass, self._async_request_schedules, SCHEDULE_POLL_INTERVAL
        )
        await self._async_request_schedules(dt_util.utcnow())
        # Settings (LED brightness, backup reserve) become available
        self.async_update_listeners()

    async def async_stop_mqtt(self) -> None:
        """Close the MQTT connection, if any."""
        if self._mqtt_refresh_unsub:
            self._mqtt_refresh_unsub()
            self._mqtt_refresh_unsub = None
        if self._energy_stream_unsub:
            self._energy_stream_unsub()
            self._energy_stream_unsub = None
        if self._schedule_poll_unsub:
            self._schedule_poll_unsub()
            self._schedule_poll_unsub = None
        self.schedules = {}
        self._schedules_known = False
        self._last_added_id = 0
        if self.mqtt:
            client, self.mqtt = self.mqtt, None
            await self.hass.async_add_executor_job(client.stop)

    async def _async_request_full_state(self, _now: datetime) -> None:
        """Ask the system for its full state again (module firmware)."""
        if self.mqtt:
            await self.hass.async_add_executor_job(self.mqtt.request_full_state)

    async def _async_request_energy_stream(self, _now: datetime) -> None:
        """Ask the system for the energy stream again (option live power)."""
        if self.mqtt:
            await self.hass.async_add_executor_job(self.mqtt.request_energy_stream)

    def _from_mqtt_thread[T](self, apply: Callable[[T], None]) -> Callable[[T], None]:
        """Return a callback that hands a value over from paho's thread."""
        return lambda value: self.hass.loop.call_soon_threadsafe(apply, value)

    @callback
    def _apply_modules(self, modules: dict[int, ModuleVersion]) -> None:
        """Show the firmware of the EMS and the smart meter on their devices."""
        if not self.mqtt:
            # Message arrived while the connection was being closed
            return
        self.modules = modules
        inverter, meter_device = self._mqtt_firmware_devices
        firmware: dict[str, str] = {}
        if ems := modules.get(MODULE_EMS):
            firmware[inverter] = ems.firmware
        if meter := modules.get(MODULE_METER):
            firmware[meter_device] = meter.firmware
        self._mqtt_firmware = firmware
        self._update_firmware(self._http_firmware)

    @property
    def _mqtt_firmware_devices(self) -> tuple[str, str]:
        """Devices whose firmware only comes from MQTT: inverter, smart meter."""
        # Device of the smart meter, see parse._read_meters
        return self.api.sn, f"{self.api.sn}_meter{MODULE_METER}"

    @callback
    def clear_mqtt_firmware(self) -> None:
        """Remove the firmware a former MQTT connection showed (option off)."""
        device_registry = dr.async_get(self.hass)
        devices = self.devices(device_registry)
        for sn in self._mqtt_firmware_devices:
            if sn in self.firmware:
                continue
            device = devices.get(sn)
            if device and device.sw_version:
                device_registry.async_update_device(device.id, sw_version=None)

    # -----------------------------
    # Settings: LED brightness, backup reserve, solar surplus, battery save mode
    # -----------------------------

    def setting_keys(
        self, name: str, values: dict[str, float | int | str] | None = None
    ) -> list[str]:
        """Return the datapoints of the inverter with a setting (emsCtrlLedBright)."""
        prefix = f"{self.api.sn}_"
        return [
            unique_id
            for unique_id in (self.data if values is None else values) or {}
            if unique_id.startswith(prefix) and unique_id.endswith(f"_{name}")
        ]

    def setting(self, name: str) -> int | None:
        """Return the current value of a setting of the inverter."""
        if name in SOC_LIMITS:
            return self.soc_limits.get(name)
        if name in MQTT_SETTINGS:
            return self.mqtt_settings.get(name)
        keys = self.setting_keys(name)
        value = self.data.get(keys[0]) if keys else None
        return value if isinstance(value, int) else None

    async def async_set_led_brightness(self, percent: int) -> None:
        """
        Set the LED brightness via MQTT and wait for the acknowledgement.

        Raises:
            HomeAssistantError: Without MQTT connection, or if the system does
                not acknowledge the command in time.

        """
        mqtt = self._connected_mqtt()
        await self._async_command(mqtt.set_led_brightness, percent)
        # Acknowledged: show the value before the system reports it
        self._apply_settings({"emsCtrlLedBright": percent})

    async def async_set_backup_reserve(self, percent: int) -> None:
        """Set the backup reserve (sysBatDsgDownLimit), see _async_set_soc_limits."""
        await self._async_set_soc_limits({"sysBatDsgDownLimit": percent})

    async def async_set_solar_surplus(self, percent: int) -> None:
        """Set the surplus solar energy delivery (devSoc), see _async_set_soc_limits."""
        await self._async_set_soc_limits({"devSoc": percent})

    async def _async_set_soc_limits(self, changes: dict[str, int]) -> None:
        """
        Change one of the SOC_LIMITS via MQTT and wait for the acknowledgement.

        One command carries charge limit, backup reserve and devSoc; the
        others are sent unchanged, so all must be known.

        Raises:
            HomeAssistantError: Without MQTT connection, without the current
                values, or if the system does not acknowledge the command in
                time.

        """
        mqtt = self._connected_mqtt()
        limits = {**self.soc_limits, **changes}
        if any(name not in limits for name in SOC_LIMITS):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="soc_limits_unknown"
            )
        await self._async_command(
            mqtt.set_soc_limits,
            limits["sysBatChgUpLimit"],
            limits["sysBatDsgDownLimit"],
            limits["devSoc"],
        )
        self._apply_settings(changes)

    async def async_set_battery_save_mode(self, *, enabled: bool) -> None:
        """
        Switch the battery save mode via MQTT and wait for the acknowledgement.

        The command carries sysZone too; it is sent unchanged, so it must be
        known.

        Raises:
            HomeAssistantError: Without MQTT connection, without sysZone, or if
                the system does not acknowledge the command in time.

        """
        mqtt = self._connected_mqtt()
        zone = self.mqtt_settings.get("sysZone")
        if zone is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="sys_zone_unknown"
            )
        await self._async_command(mqtt.set_battery_save_mode, int(enabled), zone)
        self._apply_settings({"batterySaveMode": int(enabled)})

    def _connected_mqtt(self) -> PowerOceanMqtt:
        if not self.mqtt:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="mqtt_not_connected"
            )
        return self.mqtt

    async def _async_command(self, send: Callable[..., None], *values: Any) -> None:
        """Send a command (blocking, with the seq last) and wait for the ack."""
        seq = new_seq()
        acknowledged = self.hass.loop.create_future()
        self._pending_acks[seq] = acknowledged
        try:
            await self.hass.async_add_executor_job(send, *values, seq)
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await acknowledged
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_acknowledged"
            ) from err
        finally:
            self._pending_acks.pop(seq, None)

    @callback
    def _apply_ack(self, seq: int) -> None:
        """Complete the command the system acknowledged."""
        acknowledged = self._pending_acks.get(seq)
        if acknowledged and not acknowledged.done():
            acknowledged.set_result(None)

    @callback
    def _apply_settings(self, values: dict[str, int]) -> None:
        """Show changed settings (sensors and numbers) without polling."""
        if not self.mqtt:
            return
        now = dt_util.utcnow()
        for name, value in values.items():
            self._recent_settings[name] = (value, now)
        soc_limits = {**self.soc_limits}
        mqtt_settings = {**self.mqtt_settings}
        data = {**self.data}
        for name, value in values.items():
            if name in SOC_LIMITS:
                soc_limits[name] = value
            if name in MQTT_SETTINGS:
                mqtt_settings[name] = value
            for key in self.setting_keys(name):
                data[key] = value
        if (soc_limits, mqtt_settings, data) == (
            self.soc_limits,
            self.mqtt_settings,
            self.data,
        ):
            return
        self.soc_limits = soc_limits
        self.mqtt_settings = mqtt_settings
        self.data = data
        self.async_update_listeners()

    # -----------------------------
    # Scheduled tasks (option MQTT)
    # -----------------------------

    @property
    def max_schedule_power(self) -> int | None:
        """Return the option "Max schedule power (W)", None if it is not set."""
        value = self.config_entry.options.get(CONF_MAX_SCHEDULE_POWER)
        return value if type(value) is int and value >= 1 else None

    @property
    def schedules_known(self) -> bool:
        """Return True once the system has sent its list of schedules."""
        return self._schedules_known

    @property
    def active_schedule(self) -> Schedule | None:
        """Return the schedule the system is using now, None without one."""
        return next((s for s in self.schedules.values() if s.in_use), None)

    async def _async_request_schedules(self, _now: datetime) -> None:
        """Ask the system for its schedules again (it reports no changes)."""
        if self.mqtt:
            await self.hass.async_add_executor_job(self.mqtt.request_schedules)

    @callback
    def _apply_schedules(self, schedules: list[Schedule]) -> None:
        """Take over the list of schedules, ours or the app's."""
        if not self.mqtt:
            # Message arrived while the connection was being closed
            return
        new = {schedule.schedule_id: schedule for schedule in schedules}
        changed = new != self.schedules or not self._schedules_known
        self.schedules = new
        self._schedules_known = True
        for waiter in self._schedule_waiters:
            if not waiter.done():
                waiter.set_result(None)
        if changed:
            self.async_update_listeners()

    @callback
    def _apply_schedule(self, schedule: Schedule) -> None:
        """Take over one schedule (the answer to a single read)."""
        if not self.mqtt or self.schedules.get(schedule.schedule_id) == schedule:
            return
        self.schedules = {**self.schedules, schedule.schedule_id: schedule}
        self.async_update_listeners()

    def _check_overlap(self, schedule: Schedule) -> None:
        """Refuse a time that overlaps another schedule, whatever the days."""
        for other in self.schedules.values():
            if other.schedule_id != schedule.schedule_id and windows_overlap(
                schedule.start, schedule.end, other.start, other.end
            ):
                _refuse("schedule_overlap", other=schedule_title(other))

    def _known_schedule(self, schedule_id: int) -> Schedule:
        if schedule_id not in self.schedules:
            _refuse("schedule_not_found", schedule_id=schedule_id)
        return self.schedules[schedule_id]

    async def _async_read_schedules(self, mqtt: PowerOceanMqtt) -> None:
        """Ask for the list and wait for the next one."""
        waiter = self.hass.loop.create_future()
        self._schedule_waiters.append(waiter)
        try:
            await self.hass.async_add_executor_job(mqtt.request_schedules)
            await waiter
        finally:
            self._schedule_waiters.remove(waiter)

    async def _async_confirm_schedules(
        self, mqtt: PowerOceanMqtt, shown: Callable[[dict[int, Schedule]], bool]
    ) -> None:
        """
        Read the list until it shows what a command did.

        The system acknowledges a command before its list shows the change.
        Going on with the old list would reuse an id or miss an overlap.

        Raises:
            HomeAssistantError: If the list does not show it within
                COMMAND_TIMEOUT. The command was acknowledged, so it may
                still take effect.

        """
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                while True:
                    await self._async_read_schedules(mqtt)
                    if shown(self.schedules):
                        return
                    await asyncio.sleep(SCHEDULE_CONFIRM_DELAY)
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="schedule_not_confirmed"
            ) from err

    async def async_set_schedule_enabled(
        self, schedule_id: int, *, enabled: bool
    ) -> None:
        """
        Switch a schedule on or off, wait for the acknowledgement, read again.

        Raises:
            HomeAssistantError: Without MQTT connection, for an unknown
                schedule, or if the system does not acknowledge the command.

        """
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            self._known_schedule(schedule_id)
            await self._async_command(
                mqtt.set_schedule_enabled, schedule_id, int(enabled)
            )
            await self._async_confirm_schedules(
                mqtt,
                lambda shown: (
                    schedule_id in shown and shown[schedule_id].enabled is enabled
                ),
            )

    async def async_delete_schedule(self, schedule_id: int) -> None:
        """Delete a schedule, wait for the acknowledgement, read again."""
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            self._known_schedule(schedule_id)
            await self._async_command(mqtt.delete_schedule, schedule_id)
            await self._async_confirm_schedules(
                mqtt, lambda shown: schedule_id not in shown
            )

    async def async_add_schedule(  # noqa: PLR0913
        self,
        *,
        schedule_type: int,
        power: int,
        start: int,
        end: int,
        days: int = 0,
        enabled: bool = True,
    ) -> int:
        """
        Add a schedule with the next free id and return the id.

        Start and end are minutes since midnight. `days` is 0 for daily, or a
        bit mask (Monday 1 to Sunday 64). Unknown: how the system chooses an
        id itself (the app used the next free one) and how many it accepts.

        Raises:
            HomeAssistantError: Without MQTT connection or list of schedules,
                for invalid values (see validate_schedule), or if the system
                does not acknowledge the command.

        """
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            if not self._schedules_known:
                _refuse("schedules_unknown")
            if len(self.schedules) >= MAX_SCHEDULES:
                _refuse("schedule_limit_reached", max=MAX_SCHEDULES)
            schedule = Schedule(
                schedule_id=max(max(self.schedules, default=0), self._last_added_id)
                + 1,
                schedule_type=schedule_type,
                enabled=enabled,
                power=power,
                start=start,
                end=end,
                repeat=SCHEDULE_REPEAT_DAILY if days == 0 else SCHEDULE_REPEAT_DAYS,
                days=days,
            )
            validate_schedule(schedule, self.max_schedule_power)
            self._check_overlap(schedule)
            await self._async_command(mqtt.save_schedule, SCHEDULE_ADD, schedule)
            self._last_added_id = schedule.schedule_id
            await self._async_confirm_schedules(
                mqtt,
                lambda shown: _same_schedule(shown.get(schedule.schedule_id), schedule),
            )
            return schedule.schedule_id

    async def async_update_schedule(  # noqa: PLR0913
        self,
        schedule_id: int,
        *,
        schedule_type: int | None = None,
        power: int | None = None,
        start: int | None = None,
        end: int | None = None,
        days: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """
        Change a schedule: send the whole schedule, what is not given is kept.

        The power is only checked against the option when it changes, the
        days only when they change (older schedules show repeat 65, kept as
        it is) and the time for overlaps only when it changes.

        Raises:
            HomeAssistantError: Without MQTT connection, for an unknown
                schedule, invalid values (see validate_schedule), or if the
                system does not acknowledge the command.

        """
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            current = self._known_schedule(schedule_id)
            changes: dict[str, Any] = {
                name: value
                for name, value in (
                    ("schedule_type", schedule_type),
                    ("power", power),
                    ("start", start),
                    ("end", end),
                    ("enabled", enabled),
                )
                if value is not None
            }
            if days is not None:
                changes["days"] = days
                changes["repeat"] = (
                    SCHEDULE_REPEAT_DAILY if days == 0 else SCHEDULE_REPEAT_DAYS
                )
            schedule = replace(current, **changes)
            validate_schedule(
                schedule,
                self.max_schedule_power,
                check_power=power is not None,
                check_days=days is not None,
            )
            if start is not None or end is not None:
                self._check_overlap(schedule)
            await self._async_command(mqtt.save_schedule, SCHEDULE_EDIT, schedule)
            await self._async_confirm_schedules(
                mqtt,
                lambda shown: _same_schedule(shown.get(schedule_id), schedule),
            )

    # -----------------------------
    # Live power values (option live power)
    # -----------------------------

    @callback
    def _apply_energy_stream(self, values: dict[str, float | int]) -> None:
        """Show the power values of an energy stream report right away."""
        if not self.mqtt or not self.data or self._is_dual():
            return
        self._stream_values = self._stream_datapoints(values)
        self._stream_time = dt_util.utcnow()
        data = {**self.data, **self._stream_values}
        if data == self.data:
            return
        self.data = data
        self.async_update_listeners()

    def _is_dual(self) -> bool:
        """Return True for a dual installation: its stream is not mapped yet."""
        response = self.last_response or {}
        data = response.get("data")
        return isinstance(data, dict) and bool(data.get("parallel"))

    def _stream_datapoints(
        self, values: dict[str, float | int]
    ) -> dict[str, float | int]:
        """
        Return the existing datapoints the energy stream updates, by unique_id.

        The values directly under "data" (sysLoadPwr, ...), the PV strings of
        the energy stream report and the flows calculated from the heartbeat,
        now calculated from the stream. Never adds a datapoint.
        """
        prefix = f"{self.api.sn}_"
        solar = float(values["mpptPwr"])
        by_suffix: dict[str, float | int] = {
            f"_mpptHeartBeat_{key}": value
            for key, value in energy_flows(solar, values["sysGridPwr"], values["bpPwr"])
        }
        by_suffix["_mpptHeartBeat_mpptPv_pwrTotal"] = solar
        for name in ENERGY_STREAM_STRINGS.values():
            if name in values:
                by_suffix[f"_ENERGY_STREAM_REPORT_{name}"] = values[name]

        found: dict[str, float | int] = {}
        for name, value in values.items():
            if f"{prefix}{name}" in self.data:
                found[f"{prefix}{name}"] = value
        for unique_id in self.data:
            if not unique_id.startswith(prefix):
                continue
            for suffix, value in by_suffix.items():
                if unique_id.endswith(suffix):
                    found[unique_id] = value
        return found

    def _hold_energy_stream(self, values: dict[str, float | int | str]) -> None:
        """Keep the values of a recent energy stream over the older HTTP ones."""
        if self._stream_time is None:
            return
        if dt_util.utcnow() - self._stream_time > ENERGY_STREAM_HOLD:
            self._stream_values = {}
            self._stream_time = None
            return
        for unique_id, value in self._stream_values.items():
            if unique_id in values:
                values[unique_id] = value


def _same_schedule(shown: Schedule | None, sent: Schedule) -> bool:
    """Return True if the list shows a schedule as it was sent."""
    # In use and the unknown field 2 are the system's, not part of what is sent
    return shown is not None and replace(
        shown, in_use=False, unknown_2=None
    ) == replace(sent, in_use=False, unknown_2=None)


def _refuse(key: str, **placeholders: int | str) -> NoReturn:
    """Raise the error for a refused schedule command."""
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={
            name: str(value) for name, value in placeholders.items()
        }
        or None,
    )


def validate_schedule(
    schedule: Schedule,
    max_power: int | None,
    *,
    check_power: bool = True,
    check_days: bool = True,
) -> None:
    """
    Raise HomeAssistantError if a schedule may not be sent.

    The type must be one of the app's two. Without the option "Max schedule
    power (W)" nothing is accepted; the power is a whole number from 1 to the
    option. Start and end are different times in steps of 30 minutes from
    00:00 to 23:30 (the end may be before the start: over midnight). The days
    are daily (repeat 1, days 0) or chosen (repeat 2, a mask from 1 to 127).
    Unknown, so not checked: overlapping schedules, the number of schedules.
    """
    if schedule.schedule_type not in SCHEDULE_TYPES:
        _refuse("schedule_invalid_type")
    if max_power is None:
        _refuse("max_schedule_power_not_set")
    if check_power:
        if schedule.power < 1:
            _refuse("schedule_invalid_power")
        if schedule.power > max_power:
            _refuse(
                "schedule_power_too_high", power=schedule.power, max_power=max_power
            )
    times = (schedule.start, schedule.end)
    if schedule.start == schedule.end or any(
        not 0 <= minute <= SCHEDULE_LAST_MINUTE or minute % SCHEDULE_STEP
        for minute in times
    ):
        _refuse("schedule_invalid_time")
    daily = (schedule.repeat, schedule.days) == (SCHEDULE_REPEAT_DAILY, 0)
    chosen = schedule.repeat == SCHEDULE_REPEAT_DAYS and 1 <= schedule.days <= ALL_DAYS
    if check_days and not (daily or chosen):
        _refuse("schedule_invalid_days")


def soc_limits_of(response: dict[str, Any]) -> dict[str, int]:
    """Return charge limit, backup reserve and devSoc from the reports."""
    data = response.get("data")
    quota = data.get("quota") if isinstance(data, dict) else None
    if not isinstance(quota, dict):
        return {}
    found: dict[str, int] = {}
    for report, values in quota.items():
        if not isinstance(values, dict):
            continue
        for name, suffix in SOC_LIMITS.items():
            value = values.get(name)
            if report.endswith(suffix) and type(value) is int:
                found[name] = value
    return found

"""Constants for the PowerOcean integration."""

from logging import Logger, getLogger

from homeassistant.const import Platform

from .devices import MODEL_NAME_MAP, PowerOceanModel  # noqa: F401 (re-export)

LOGGER: Logger = getLogger(__package__)
DOMAIN = "powerocean_single_phase"
DEFAULT_SCAN_INTERVAL = 10
DEFAULT_NAME = "PowerOcean"
# Option: additional MQTT connection to the EcoFlow cloud (off by default)
CONF_MQTT = "mqtt"
# Option: power values via MQTT every ~5 s (needs CONF_MQTT, off by default)
CONF_LIVE_POWER = "live_power"
# Option: highest power in W a schedule may use (no default; unset blocks schedules)
CONF_MAX_SCHEDULE_POWER = "max_schedule_power"
# EcoFlow web portal for owners (portal.ecoflow.com is the installer portal)
PORTAL_URL = "https://user-portal.ecoflow.com/"
PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

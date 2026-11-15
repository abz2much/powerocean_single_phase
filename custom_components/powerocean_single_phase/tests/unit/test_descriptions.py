"""The descriptions and hints that users see are in English."""

import pytest

from custom_components.powerocean_single_phase.sensor import ERROR_DETAILS_HINT
from custom_components.powerocean_single_phase.types import SensorMetaHelper


@pytest.mark.parametrize(
    ("key", "description"),
    [
        ("sysLoadPwr", "House grid"),
        ("housePower", "House grid (calculated)"),
        ("sysGridPwr", "Power grid"),
        ("gridPower", "Grid power"),
        ("gridToBattery", "Grid to battery"),
        ("gridToHouse", "Grid to house"),
        ("batteryToHouse", "Battery to house"),
        ("batteryToGrid", "Battery to grid"),
        ("solarToBattery", "Solar to battery"),
        ("solarToGrid", "Solar to grid"),
        ("solarToHouse", "Solar to house"),
        ("mpptPwr", "Solar yield"),
        ("bpPwr", "Battery power"),
        ("bpSoc", "Battery state of charge"),
        ("bpVol", "Battery voltage"),
        ("bpAmp", "Battery current"),
        ("bpCycles", "Charge cycles"),
        ("bpTemp", "Battery cell temperature"),
        ("errorCount", "Active errors"),
        ("createTime", "Installation date"),
        (
            "mppt3FaultCode",
            "Error code PV string 3 (meaning not published; usually 2 at night)",
        ),
    ],
)
def test_description(key: str, description: str) -> None:
    assert SensorMetaHelper.get_description(key) == description


def test_error_details_hint() -> None:
    assert ERROR_DETAILS_HINT.startswith(
        "Cause and solution in the EcoFlow web portal: https://"
    )

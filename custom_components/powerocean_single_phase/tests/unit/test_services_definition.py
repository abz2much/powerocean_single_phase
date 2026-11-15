"""test_services_definition: services.yaml and the strings describe the same actions."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

COMPONENT_DIR = Path(__file__).parent.parent.parent
SERVICES: dict[str, Any] = yaml.safe_load(
    (COMPONENT_DIR / "services.yaml").read_text("utf-8")
)
FILES = ["strings.json", "translations/en.json"]


def _texts(file: str) -> dict[str, Any]:
    return json.loads((COMPONENT_DIR / file).read_text("utf-8"))


def test_the_three_actions() -> None:
    assert set(SERVICES) == {"add_schedule", "update_schedule", "delete_schedule"}


def test_the_targets() -> None:
    # Add: the inverter device (there may be no schedule yet to point at)
    assert SERVICES["add_schedule"]["target"] == {
        "device": {"integration": "powerocean_single_phase"}
    }
    for name in ("update_schedule", "delete_schedule"):
        assert SERVICES[name]["target"] == {
            "entity": {"integration": "powerocean_single_phase", "domain": "switch"}
        }


def test_add_needs_type_power_start_and_end() -> None:
    fields = SERVICES["add_schedule"]["fields"]

    required = {name for name, field in fields.items() if field.get("required")}
    assert required == {"type", "power", "start", "end"}


@pytest.mark.parametrize("file", FILES)
def test_every_action_and_field_has_a_name_and_description(file: str) -> None:
    texts = _texts(file)["services"]

    assert set(texts) == set(SERVICES)
    for name, definition in SERVICES.items():
        assert texts[name]["name"]
        assert texts[name]["description"]
        fields = definition.get("fields", {})
        assert set(texts[name].get("fields", {})) == set(fields), name
        for field, text in texts[name].get("fields", {}).items():
            assert text["name"], f"{name}.{field}"
            assert text["description"], f"{name}.{field}"


@pytest.mark.parametrize("file", FILES)
def test_selector_options_are_translated(file: str) -> None:
    translated = _texts(file)["selector"]

    for name, definition in SERVICES.items():
        for field, spec in definition.get("fields", {}).items():
            select = spec.get("selector", {}).get("select")
            if not select:
                continue
            options = translated[select["translation_key"]]["options"]
            assert set(options) == set(select["options"]), f"{name}.{field}"


@pytest.mark.parametrize("file", FILES)
def test_the_error_texts_of_the_actions_exist(file: str) -> None:
    exceptions = _texts(file)["exceptions"]

    for key in (
        "schedule_target",
        "schedule_days_and_daily",
        "schedule_invalid_time",
        "schedule_invalid_power",
        "schedule_overlap",
        "schedule_limit_reached",
        "schedule_not_confirmed",
    ):
        assert exceptions[key]["message"], key


def test_strings_and_translation_have_the_same_actions() -> None:
    assert (
        _texts("strings.json")["services"] == _texts("translations/en.json")["services"]
    )
    assert (
        _texts("strings.json")["selector"] == _texts("translations/en.json")["selector"]
    )

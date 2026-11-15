"""The domain, the manifest and the folder name agree."""

import json
from pathlib import Path

from custom_components.powerocean_single_phase.const import DOMAIN

COMPONENT_DIR = Path(__file__).parent.parent.parent


def test_domain_name() -> None:
    assert DOMAIN == "powerocean_single_phase"


def test_manifest_and_folder_use_the_domain() -> None:
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text("utf-8"))
    assert manifest["domain"] == DOMAIN
    assert COMPONENT_DIR.name == DOMAIN

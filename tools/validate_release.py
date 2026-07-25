"""Structural release validation without requiring a Home Assistant checkout."""

from __future__ import annotations

import importlib.util
import json
import py_compile
import re
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components"
COMPONENT = COMPONENT_ROOT / "hoymiles_hit_modbus"
RESOURCES = COMPONENT / "resources"


def require(condition: bool, message: str) -> None:
    """Raise a readable release validation error."""
    if not condition:
        raise RuntimeError(message)


def load_json(path: Path) -> dict | list:
    """Load and validate UTF-8 JSON."""
    with path.open(encoding="utf-8") as json_file:
        return json.load(json_file)


def load_localization_module():
    """Load the standalone localization module without Home Assistant."""
    path = COMPONENT / "localization.py"
    spec = importlib.util.spec_from_file_location("hoymiles_localization", path)
    require(spec is not None and spec.loader is not None, "Cannot load localization")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entity_translation_keys(translations: dict) -> dict[str, set[str]]:
    """Return translation keys grouped by entity domain."""
    return {
        domain: set(entries)
        for domain, entries in translations.get("entity", {}).items()
    }


def main() -> int:
    """Validate HACS layout, translations, Python and bundled assets."""
    integration_dirs = [
        path for path in COMPONENT_ROOT.iterdir() if path.is_dir()
    ]
    require(
        integration_dirs == [COMPONENT],
        "HACS repositories may contain only one custom integration",
    )

    hacs = load_json(ROOT / "hacs.json")
    require(hacs.get("name"), "hacs.json requires a display name")

    manifest = load_json(COMPONENT / "manifest.json")
    required_manifest = {
        "domain",
        "documentation",
        "issue_tracker",
        "codeowners",
        "name",
        "version",
    }
    require(
        required_manifest <= set(manifest),
        f"manifest.json is missing: {sorted(required_manifest - set(manifest))}",
    )
    require(manifest["domain"] == "hoymiles_hit_modbus", "Unexpected domain")
    require(manifest["version"] == "1.0.0", "Release version must be 1.0.0")

    catalog = load_json(COMPONENT / "entity_catalog.json")
    require(isinstance(catalog, list), "Entity catalog must be a list")
    require(len(catalog) >= 250, "The generated catalog is unexpectedly small")
    identities = {
        (entry["domain"], entry["translation_key"]) for entry in catalog
    }
    require(
        len(identities) == len(catalog),
        "Duplicate domain/translation_key in entity catalog",
    )

    english = load_json(COMPONENT / "translations" / "en.json")
    polish = load_json(COMPONENT / "translations" / "pl.json")
    require(
        entity_translation_keys(english) == entity_translation_keys(polish),
        "English and Polish entity translation keys differ",
    )
    for entry in catalog:
        domain = entry["domain"]
        key = entry["translation_key"]
        require(key in english["entity"][domain], f"Missing English key {domain}.{key}")
        require(key in polish["entity"][domain], f"Missing Polish key {domain}.{key}")

    required_assets = [
        RESOURCES / "dashboard_hoymiles_en.yaml",
        RESOURCES / "dashboard_hoymiles_pl.yaml",
        RESOURCES / "home_assistant" / "en" / "hoymiles_ems_scheduler.yaml",
        RESOURCES / "home_assistant" / "pl" / "hoymiles_ems_scheduler.yaml",
        RESOURCES / "www" / "hoymiles-rce-chart-card.js",
    ]
    for asset in required_assets:
        require(asset.is_file(), f"Missing bundled asset: {asset.relative_to(ROOT)}")

    for asset in required_assets[:4]:
        text = asset.read_text(encoding="utf-8")
        require(
            not re.search(
                r"\b(?:sensor|number|select)\.[a-z0-9_]*hoymiles_inverter[a-z0-9_]*\b",
                text,
            ),
            f"Installation-specific entity id remains in {asset.name}",
        )
        require(
            not re.search(r"\bPV[56]\b", text, flags=re.IGNORECASE),
            f"Unsupported PV5/PV6 reference remains in {asset.name}",
        )

    english_assets = [required_assets[0], required_assets[2]]
    polish_characters = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
    for asset in english_assets:
        visible_lines = [
            line
            for line in asset.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        require(
            not polish_characters.search("\n".join(visible_lines)),
            f"Visible Polish text remains in English asset {asset.name}",
        )

    for python_file in COMPONENT.glob("*.py"):
        py_compile.compile(python_file, doraise=True)

    localization = load_localization_module()
    require(
        localization.localized_text_state("Praca z siecią", "en") == "On-grid operation",
        "English text state localization failed",
    )
    require(
        localization.localized_text_state("Praca z siecią", "pl") == "Praca z siecią",
        "Polish text state localization failed",
    )
    require(
        localization.localized_text_state("Brak błędu", "en") == "No error",
        "English fault state localization failed",
    )

    for image_name in ("icon.png", "dark_icon.png", "logo.png", "dark_logo.png"):
        image_path = COMPONENT / "brand" / image_name
        require(image_path.is_file(), f"Missing brand asset {image_name}")
        with Image.open(image_path) as image:
            require(image.width >= 128 and image.height >= 128, f"{image_name} is too small")

    print(f"HACS layout: OK ({len(integration_dirs)} integration)")
    print(f"Manifest: OK (version {manifest['version']})")
    print(f"Localized entities: {len(catalog)} (English and Polish)")
    print("Bundled dashboards/EMS assets: OK")
    print("Python syntax: OK")
    print("Text-state localization: OK")
    print("Brand assets: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

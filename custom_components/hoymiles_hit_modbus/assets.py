"""Install optional dashboard and EMS assets into Home Assistant config."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant


RESOURCE_ROOT = Path(__file__).with_name("resources")
CATALOG_PATH = Path(__file__).with_name("entity_catalog.json")
LEGACY_ENTITY_BACKUP_SUFFIX = ".pre-stable-entity-ids.bak"
ENTITY_ID_PATTERN = re.compile(
    r"\b(button|sensor|number|select)\.([a-z0-9_]+)\b"
)


def _stable_entity_id_map() -> dict[tuple[str, str], str]:
    """Return source object ids mapped to stable integration entity ids."""
    with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        catalog = json.load(catalog_file)
    return {
        (record["domain"], record["source_object_id"]): (
            f"{record['domain']}.hoymiles_hit_{record['translation_key']}"
        )
        for record in catalog
    }


def _migrate_legacy_entity_ids(path: Path) -> bool:
    """Replace device-name-dependent ids while preserving the user asset."""
    if not path.is_file():
        return False

    original = path.read_text(encoding="utf-8")
    stable_ids = _stable_entity_id_map()

    def replace(match: re.Match[str]) -> str:
        domain, object_id = match.groups()
        if "hoymiles_inverter" not in object_id:
            return match.group(0)
        for (candidate_domain, source_object_id), stable_id in stable_ids.items():
            if candidate_domain != domain:
                continue
            if object_id == source_object_id or object_id.endswith(
                f"_{source_object_id}"
            ):
                return stable_id
        return match.group(0)

    migrated = ENTITY_ID_PATTERN.sub(replace, original)
    if migrated == original:
        return False

    backup = path.with_name(f"{path.name}{LEGACY_ENTITY_BACKUP_SUFFIX}")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(migrated, encoding="utf-8")
    return True


def _copy_assets(config_path: Path, language: str, overwrite: bool) -> list[Path]:
    """Copy bundled assets and return paths that were written."""
    localized = "pl" if language.startswith("pl") else "en"
    sources = {
        RESOURCE_ROOT / f"dashboard_hoymiles_{localized}.yaml": (
            config_path / "dashboard_hoymiles.yaml"
        ),
        RESOURCE_ROOT
        / "home_assistant"
        / localized
        / "hoymiles_ems_scheduler.yaml": (
            config_path / "packages" / "hoymiles_ems_scheduler.yaml"
        ),
        RESOURCE_ROOT / "www" / "hoymiles-rce-chart-card.js": (
            config_path / "www" / "hoymiles-rce-chart-card.js"
        ),
        RESOURCE_ROOT / "www" / "hoymiles-inverter.png": (
            config_path / "www" / "hoymiles-inverter.png"
        ),
    }
    written: list[Path] = []
    for source, destination in sources.items():
        if destination.exists() and not overwrite:
            if (
                destination.name
                in {"dashboard_hoymiles.yaml", "hoymiles_ems_scheduler.yaml"}
                and _migrate_legacy_entity_ids(destination)
            ):
                written.append(destination)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        written.append(destination)
    return written


async def async_install_assets(
    hass: HomeAssistant,
    *,
    overwrite: bool,
) -> list[Path]:
    """Install the optional assets without blocking Home Assistant."""
    config_path = Path(hass.config.config_dir)
    return await hass.async_add_executor_job(
        _copy_assets,
        config_path,
        hass.config.language,
        overwrite,
    )

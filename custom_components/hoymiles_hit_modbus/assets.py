"""Install optional dashboard and EMS assets into Home Assistant config."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, VERSION


RESOURCE_ROOT = Path(__file__).with_name("resources")
CATALOG_PATH = Path(__file__).with_name("entity_catalog.json")
LEGACY_ENTITY_BACKUP_SUFFIX = ".pre-stable-entity-ids.bak"
ENTITY_ID_PATTERN = re.compile(
    r"\b(button|sensor|number|select)\.([a-z0-9_]+)\b"
)
ASSET_STORAGE_VERSION = 1
ASSET_STORAGE_KEY = f"{DOMAIN}.assets"
LOVELACE_RESOURCES_KEY = "lovelace_resources"
LOVELACE_STORAGE_PREFIX = "lovelace."
ZEBRA_CARD_TYPE = "custom:hoymiles-zebra-entities-card"
FRONTEND_ASSET_REVISION = 8
FRONTEND_STATIC_ROUTE = "static-r2"
FRONTEND_RESOURCE_URL = (
    f"/api/{DOMAIN}/{FRONTEND_STATIC_ROUTE}/hoymiles-rce-chart-card.js"
    f"?v={VERSION}.{FRONTEND_ASSET_REVISION}"
)
FRONTEND_BOOTSTRAP_URL = (
    f"/api/{DOMAIN}/{FRONTEND_STATIC_ROUTE}/hoymiles-dashboard-strategy.js"
    f"?v={VERSION}.{FRONTEND_ASSET_REVISION}"
)
MANAGED_FRONTEND_RESOURCE_PATHS = {
    "/local/hoymiles-rce-chart-card.js",
    f"/api/{DOMAIN}/static/hoymiles-rce-chart-card.js",
    f"/api/{DOMAIN}/{FRONTEND_STATIC_ROUTE}/hoymiles-rce-chart-card.js",
}
HOYMILES_DASHBOARD_MARKERS = (
    "hoymiles_hit_overview_pv_total_power",
    "hoymiles_hit_overview_battery_power",
)
LEGACY_INVERTER_IMAGE_PATHS = {
    f"/api/{DOMAIN}/static/hoymiles-inverter.png",
    f"/api/{DOMAIN}/{FRONTEND_STATIC_ROUTE}/hoymiles-inverter.png",
}
INVERTER_IMAGE_PATH = "/local/hoymiles-inverter.png"
RCE_LOAD_ROW_LABELS = {
    "pl": {
        "sensor.hoymiles_actual_load_energy_today": (
            "Rzeczywiste zużycie odbiorników dzisiaj"
        ),
        "sensor.hoymiles_rce_pv_self_consumption_today": (
            "PV → odbiorniki — rejestr diagnostyczny"
        ),
        "sensor.hoymiles_rce_battery_to_load_today": (
            "Energia oddana przez baterię — diagnostycznie"
        ),
        "sensor.hoymiles_rce_grid_to_load_today": (
            "Energia pobrana z sieci — diagnostycznie"
        ),
    },
    "en": {
        "sensor.hoymiles_actual_load_energy_today": (
            "Actual load consumption today"
        ),
        "sensor.hoymiles_rce_pv_self_consumption_today": (
            "PV to load — diagnostic register"
        ),
        "sensor.hoymiles_rce_battery_to_load_today": (
            "Battery energy output — diagnostic"
        ),
        "sensor.hoymiles_rce_grid_to_load_today": (
            "Grid energy input — diagnostic"
        ),
    },
}

# v1.2.0 was the last release without managed-asset metadata. These checksums
# let the first newer release upgrade untouched files while preserving user
# modifications.
LEGACY_MANAGED_HASHES: dict[str, set[str]] = {
    "dashboard_hoymiles.yaml": {
        "86d43b9126b16e1fcb710e298ac80f8793e9a08e377105c6fa1c26c96e8a5d7f",
        "1cdf745154d565ce2dddb8c8a64c96075a1fac813171872445e716521199d21a",
    },
    "packages/hoymiles_ems_scheduler.yaml": {
        "6df876b47f18223ce905e0cc052921393325703f1ecef31d013dbe1aec237750",
        "82d3250cf87b316d7eb95e3ed1939e6bdf8f2845c17e9549f1b7d270490cf09a",
    },
    "www/hoymiles-rce-chart-card.js": {
        "bdc80c03d40d811835697f4d5c126ec90a8a6f2c59be9bdd29de1c05b96f09a6",
    },
    "www/hoymiles-inverter.png": {
        "4531e85081e78cf94dee82dbde75b2f931860886457ce9800f546afb4a3b3d15",
    },
}


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


def _sha256(path: Path) -> str:
    """Return a file SHA-256 digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    """Copy a managed asset without exposing a partially written file."""
    temporary = destination.with_name(f".{destination.name}.{DOMAIN}.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a Home Assistant storage document atomically."""
    temporary = path.with_name(f".{path.name}.{DOMAIN}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if path.exists():
        shutil.copystat(path, temporary)
    os.replace(temporary, path)


def _backup_storage_once(path: Path) -> None:
    """Keep one exact rollback copy for this integration release."""
    backup = path.with_name(f"{path.name}.pre-{VERSION}.bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def _replace_entities_cards(value: Any) -> int:
    """Convert native entities cards to the drop-in zebra card in place."""
    changed = 0
    if isinstance(value, dict):
        if value.get("type") == "entities":
            value["type"] = ZEBRA_CARD_TYPE
            changed += 1
        for child in value.values():
            changed += _replace_entities_cards(child)
    elif isinstance(value, list):
        for child in value:
            changed += _replace_entities_cards(child)
    return changed


def _migrate_rce_load_rows(value: Any, language: str) -> int:
    """Expose the physical LOAD counter and clarify legacy flow estimates."""
    changed = 0
    localized = "pl" if language.startswith("pl") else "en"
    labels = RCE_LOAD_ROW_LABELS[localized]
    pv_entity = "sensor.hoymiles_rce_pv_self_consumption_today"
    actual_entity = "sensor.hoymiles_actual_load_energy_today"

    if isinstance(value, dict):
        entity = value.get("entity")
        if entity in labels and value.get("name") != labels[entity]:
            value["name"] = labels[entity]
            changed += 1

        entities = value.get("entities")
        if isinstance(entities, list):
            entity_ids = {
                row.get("entity")
                for row in entities
                if isinstance(row, dict)
            }
            if pv_entity in entity_ids and actual_entity not in entity_ids:
                for index, row in enumerate(entities):
                    if isinstance(row, dict) and row.get("entity") == pv_entity:
                        entities.insert(
                            index,
                            {
                                "entity": actual_entity,
                                "name": labels[actual_entity],
                            },
                        )
                        changed += 1
                        break

        for child in value.values():
            changed += _migrate_rce_load_rows(child, language)
    elif isinstance(value, list):
        for child in value:
            changed += _migrate_rce_load_rows(child, language)
    return changed


def _is_hoymiles_dashboard(config: Any) -> bool:
    """Return whether a Lovelace config is the managed Hoymiles dashboard."""
    if not isinstance(config, dict):
        return False
    serialized = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    return all(marker in serialized for marker in HOYMILES_DASHBOARD_MARKERS)


def _sync_lovelace_resource(storage_path: Path) -> bool:
    """Install or cache-bust the managed Hoymiles frontend module."""
    path = storage_path / LOVELACE_RESOURCES_KEY
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
    else:
        # A fresh HA installation may not have a Lovelace resource store yet.
        # Relying only on add_extra_js_url creates a startup race: Lovelace can
        # request the dashboard strategy before the integration module has
        # registered its custom element.  Creating the standard resource store
        # makes HA load the module before generating the managed dashboard.
        payload = {
            "version": 1,
            "minor_version": 1,
            "key": LOVELACE_RESOURCES_KEY,
            "data": {"items": []},
        }

    items = payload.get("data", {}).get("items")
    if not isinstance(items, list):
        return False

    matched = False
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        base_url = url.partition("?")[0]
        if base_url not in MANAGED_FRONTEND_RESOURCE_PATHS:
            continue
        matched = True
        if url != FRONTEND_RESOURCE_URL or item.get("type") != "module":
            item["url"] = FRONTEND_RESOURCE_URL
            item["type"] = "module"
            changed = True

    if not matched:
        resource_id = hashlib.sha256(
            f"{DOMAIN}:frontend".encode()
        ).hexdigest()[:32]
        items.append(
            {
                "id": resource_id,
                "url": FRONTEND_RESOURCE_URL,
                "type": "module",
            }
        )
        changed = True

    if not changed:
        return False
    _backup_storage_once(path)
    _atomic_write_json(path, payload)
    return True


def _sync_lovelace_storage(
    config_path: Path,
    language: str = "pl",
) -> list[Path]:
    """Migrate active storage dashboards without replacing user layouts."""
    storage_path = config_path / ".storage"
    if not storage_path.is_dir():
        return []

    written: list[Path] = []
    if _sync_lovelace_resource(storage_path):
        written.append(storage_path / LOVELACE_RESOURCES_KEY)

    for path in sorted(storage_path.glob(f"{LOVELACE_STORAGE_PREFIX}*")):
        if not path.is_file() or path.name == LOVELACE_RESOURCES_KEY:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Backups share the same embedded key as the active file. Requiring
        # an exact key/path match prevents recursively migrating them.
        if payload.get("key") != path.name:
            continue
        config = payload.get("data", {}).get("config")
        if not _is_hoymiles_dashboard(config):
            continue
        changes = _replace_entities_cards(config)
        changes += _migrate_rce_load_rows(config, language)
        changes += _migrate_inverter_image_paths(config)
        if changes == 0:
            continue
        _backup_storage_once(path)
        _atomic_write_json(path, payload)
        written.append(path)
    return written


def _migrate_inverter_image_paths(value: Any) -> int:
    """Move dashboards away from removed integration-static image routes."""
    changes = 0
    if isinstance(value, dict):
        image = value.get("inverter_image")
        if image in LEGACY_INVERTER_IMAGE_PATHS:
            value["inverter_image"] = INVERTER_IMAGE_PATH
            changes += 1
        for child in value.values():
            changes += _migrate_inverter_image_paths(child)
    elif isinstance(value, list):
        for child in value:
            changes += _migrate_inverter_image_paths(child)
    return changes


def _sync_assets(
    config_path: Path,
    language: str,
    overwrite: bool,
    managed_hashes: dict[str, str] | None,
) -> tuple[list[Path], dict[str, str]]:
    """Install assets and safely update files deployed by an older release."""
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
    previous_hashes = managed_hashes or {}
    next_hashes: dict[str, str] = {}
    for source, destination in sources.items():
        relative = destination.relative_to(config_path).as_posix()
        source_hash = _sha256(source)
        if destination.exists() and not overwrite:
            destination_hash = _sha256(destination)
            managed = (
                previous_hashes.get(relative) == destination_hash
                or destination_hash in LEGACY_MANAGED_HASHES.get(relative, set())
            )
            if managed and destination_hash != source_hash:
                _atomic_copy(source, destination)
                written.append(destination)
                destination_hash = source_hash
            elif managed:
                next_hashes[relative] = source_hash
                continue

            if (
                destination.name
                in {"dashboard_hoymiles.yaml", "hoymiles_ems_scheduler.yaml"}
                and _migrate_legacy_entity_ids(destination)
            ):
                written.append(destination)
                destination_hash = _sha256(destination)
            if destination_hash == source_hash:
                next_hashes[relative] = source_hash
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_copy(source, destination)
        written.append(destination)
        next_hashes[relative] = source_hash
    return written, next_hashes


def _copy_assets(config_path: Path, language: str, overwrite: bool) -> list[Path]:
    """Compatibility wrapper used by structural release tests."""
    written, _ = _sync_assets(config_path, language, overwrite, None)
    return written


async def async_install_assets(
    hass: HomeAssistant,
    *,
    overwrite: bool,
) -> list[Path]:
    """Install the optional assets without blocking Home Assistant."""
    config_path = Path(hass.config.config_dir)
    store: Store[dict] = Store(
        hass,
        ASSET_STORAGE_VERSION,
        ASSET_STORAGE_KEY,
    )
    stored = await store.async_load() or {}
    managed_hashes = stored.get("assets", {})
    written, next_hashes = await hass.async_add_executor_job(
        _sync_assets,
        config_path,
        hass.config.language,
        overwrite,
        managed_hashes,
    )
    written.extend(
        await hass.async_add_executor_job(
            _sync_lovelace_storage,
            config_path,
            hass.config.language,
        )
    )
    await store.async_save(
        {
            "integration_version": VERSION,
            "assets": next_hashes,
        }
    )
    return written

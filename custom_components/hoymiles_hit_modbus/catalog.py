"""Load and match the localized entity catalog."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import SUPPORTED_SOURCE_DOMAINS
from .models import MatchedEntity


CATALOG_PATH = Path(__file__).with_name("entity_catalog.json")


def _slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("%", " percent ")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


@cache
def load_catalog() -> tuple[dict[str, Any], ...]:
    """Return the generated entity catalog."""
    with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        return tuple(json.load(catalog_file))


def _entry_object_id(entry: er.RegistryEntry) -> str:
    return entry.entity_id.split(".", 1)[1]


def _name_matches(
    entry: er.RegistryEntry,
    catalog: dict[str, Any],
    source_device: dr.DeviceEntry,
) -> bool:
    """Match renamed devices without depending on their entity-id prefix."""
    source_name = catalog["source_name"]
    source_object_id = catalog["source_object_id"]
    original_name = entry.original_name or ""
    if original_name.casefold() == source_name.casefold():
        return True

    object_id = _entry_object_id(entry)
    if object_id == source_object_id or object_id.endswith(f"_{source_object_id}"):
        return True

    device_names = {
        source_device.name or "",
        source_device.name_by_user or "",
        "Hoymiles Inverter",
    }
    friendly_name = original_name
    for device_name in sorted(device_names, key=len, reverse=True):
        if device_name and friendly_name.casefold().startswith(
            f"{device_name} ".casefold()
        ):
            friendly_name = friendly_name[len(device_name) + 1 :]
            break
    return _slugify(friendly_name) == source_object_id


async def async_match_entities(
    hass: HomeAssistant,
    source_device_id: str,
) -> tuple[dr.DeviceEntry | None, dict[str, list[MatchedEntity]]]:
    """Match ESPHome entities and retain placeholders for newer firmware."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    source_device = device_registry.async_get(source_device_id)
    matched: dict[str, list[MatchedEntity]] = {
        domain: [] for domain in SUPPORTED_SOURCE_DOMAINS
    }
    if source_device is None:
        return None, matched

    source_entries = [
        entry
        for entry in er.async_entries_for_device(
            entity_registry,
            source_device_id,
            include_disabled_entities=True,
        )
        if entry.platform == "esphome"
        and entry.domain in SUPPORTED_SOURCE_DOMAINS
    ]
    used_entity_ids: set[str] = set()

    catalogs = await hass.async_add_executor_job(load_catalog)
    for catalog in catalogs:
        domain = catalog["domain"]
        matched_source: er.RegistryEntry | None = None
        for source in source_entries:
            if (
                source.entity_id not in used_entity_ids
                and source.domain == domain
                and _name_matches(source, catalog, source_device)
            ):
                matched_source = source
                used_entity_ids.add(source.entity_id)
                break
        matched[domain].append(
            MatchedEntity(catalog=catalog, source=matched_source)
        )

    return source_device, matched


def matched_source_count(matched: dict[str, list[MatchedEntity]]) -> int:
    """Return the number of catalog records backed by ESPHome entities."""
    return sum(
        entity.source is not None
        for entities in matched.values()
        for entity in entities
    )

"""Data models for the Hoymiles HIT xxL G3 Modbus integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


@dataclass(slots=True)
class MatchedEntity:
    """A localized catalog entry matched to an ESPHome source entity."""

    catalog: dict[str, Any]
    source: er.RegistryEntry


@dataclass(slots=True)
class RuntimeData:
    """Runtime data shared by all entity platforms."""

    source_device: dr.DeviceEntry
    entities: dict[str, list[MatchedEntity]]

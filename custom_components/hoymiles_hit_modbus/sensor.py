"""Localized Hoymiles sensor entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import HoymilesProxyEntity
from .localization import localized_text_state
from .models import RuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up localized sensors."""
    runtime: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HoymilesSensor(hass, entry, runtime, matched)
        for matched in runtime.entities["sensor"]
    )


class HoymilesSensor(HoymilesProxyEntity, SensorEntity):
    """A sensor mirrored from the ESPHome Modbus bridge."""

    @property
    def native_value(self) -> Any:
        """Return a numeric value or localized text state."""
        source = self.source_state
        if source is None or not self.available:
            return None
        if self._catalog.get("source_component") == "text_sensor":
            return localized_text_state(source.state, self.hass.config.language)
        try:
            return float(source.state)
        except (TypeError, ValueError):
            return source.state

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Mirror the source unit."""
        source = self.source_state
        return source.attributes.get("unit_of_measurement") if source else None

    @property
    def device_class(self) -> str | None:
        """Mirror the source device class."""
        source = self.source_state
        return source.attributes.get("device_class") if source else None

    @property
    def state_class(self) -> str | None:
        """Mirror the source state class."""
        source = self.source_state
        return source.attributes.get("state_class") if source else None

    @property
    def suggested_display_precision(self) -> int | None:
        """Mirror the source display precision when available."""
        source = self.source_state
        if source is None:
            return None
        precision = source.attributes.get("suggested_display_precision")
        return int(precision) if precision is not None else None

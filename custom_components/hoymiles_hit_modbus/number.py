"""Localized writable Hoymiles number entities."""

from __future__ import annotations

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import HoymilesProxyEntity
from .models import RuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable localized number entities."""
    runtime: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HoymilesNumber(hass, entry, runtime, matched)
        for matched in runtime.entities["number"]
    )


class HoymilesNumber(HoymilesProxyEntity, NumberEntity):
    """A number mirrored from the ESPHome Modbus bridge."""

    @property
    def native_value(self) -> float | None:
        """Return the current number."""
        source = self.source_state
        if source is None or not self.available:
            return None
        try:
            return float(source.state)
        except (TypeError, ValueError):
            return None

    @property
    def native_min_value(self) -> float:
        """Return the minimum accepted value."""
        source = self.source_state
        return float(source.attributes.get("min", 0)) if source else 0

    @property
    def native_max_value(self) -> float:
        """Return the maximum accepted value."""
        source = self.source_state
        return float(source.attributes.get("max", 100)) if source else 100

    @property
    def native_step(self) -> float:
        """Return the number step."""
        source = self.source_state
        return float(source.attributes.get("step", 1)) if source else 1

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Mirror the source unit."""
        source = self.source_state
        return source.attributes.get("unit_of_measurement") if source else None

    @property
    def mode(self) -> NumberMode:
        """Mirror the source input mode."""
        source = self.source_state
        raw_mode = source.attributes.get("mode", NumberMode.AUTO.value) if source else NumberMode.AUTO.value
        try:
            return NumberMode(raw_mode)
        except ValueError:
            return NumberMode.AUTO

    async def async_set_native_value(self, value: float) -> None:
        """Forward a value write to ESPHome."""
        if self._source_entity_id is None:
            raise HomeAssistantError(
                "This setting requires a newer Hoymiles ESPHome firmware"
            )
        await self.hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {
                ATTR_ENTITY_ID: self._source_entity_id,
                ATTR_VALUE: value,
            },
            blocking=True,
            context=self._context,
        )

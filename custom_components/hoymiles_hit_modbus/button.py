"""Localized writable Hoymiles button entities."""

from __future__ import annotations

from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
    SERVICE_PRESS,
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
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
    """Set up one-shot localized button entities."""
    runtime: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HoymilesButton(hass, entry, runtime, matched)
        for matched in runtime.entities["button"]
    )


class HoymilesButton(HoymilesProxyEntity, ButtonEntity):
    """A button that forwards a single press to the ESPHome bridge."""

    @property
    def available(self) -> bool:
        """Buttons remain available before their first press."""
        source = self.source_state
        return source is not None and source.state != STATE_UNAVAILABLE

    async def async_press(self) -> None:
        """Forward a button press to ESPHome."""
        if self._source_entity_id is None:
            raise HomeAssistantError(
                "This command requires a newer Hoymiles ESPHome firmware"
            )
        await self.hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: self._source_entity_id},
            blocking=True,
            context=self._context,
        )

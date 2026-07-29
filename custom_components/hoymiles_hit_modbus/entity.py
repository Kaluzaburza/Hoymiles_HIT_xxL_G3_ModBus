"""Base localized proxy entity."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, NAME
from .models import MatchedEntity, RuntimeData


class HoymilesProxyEntity(Entity):
    """Base entity that mirrors a native ESPHome entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: RuntimeData,
        matched: MatchedEntity,
    ) -> None:
        """Initialize a localized proxy entity."""
        self.hass = hass
        self._entry = entry
        self._runtime = runtime
        self._matched = matched
        self._source_entity_id = (
            matched.source.entity_id if matched.source is not None else None
        )
        self._catalog = matched.catalog

        self._attr_translation_key = self._catalog["translation_key"]
        self._attr_unique_id = (
            f"{entry.entry_id}_{self._catalog['translation_key']}"
        )
        # Firmware and the HACS integration can be updated independently.
        # Keep every catalog entity enabled so dashboards see an unavailable
        # entity instead of a missing one until ESPHome is upgraded.
        self._attr_entity_registry_enabled_default = True
        category = self._catalog.get("entity_category")
        if category in {EntityCategory.CONFIG.value, EntityCategory.DIAGNOSTIC.value}:
            self._attr_entity_category = EntityCategory(category)

    @property
    def suggested_object_id(self) -> str:
        """Return a stable object id independent of language and device name."""
        return f"hoymiles_hit_{self._catalog['translation_key']}"

    @property
    def source_state(self) -> State | None:
        """Return the source entity state."""
        if self._source_entity_id is None:
            return None
        return self.hass.states.get(self._source_entity_id)

    @property
    def available(self) -> bool:
        """Return whether the source ESPHome entity is available."""
        source = self.source_state
        return (
            source is not None
            and source.state not in {STATE_UNKNOWN, STATE_UNAVAILABLE, "Niedostępne"}
        )

    @property
    def icon(self) -> str | None:
        """Mirror the source icon."""
        source = self.source_state
        return source.attributes.get("icon") if source else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose traceability and an editable first-pass description."""
        language = "pl" if self.hass.config.language.startswith("pl") else "en"
        return {
            "source_entity_id": self._source_entity_id,
            "description": self._catalog["description"][language],
            "firmware_update_required": self._source_entity_id is None,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return the localized integration device."""
        source = self._runtime.source_device
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=source.name_by_user or source.name or NAME,
            manufacturer=source.manufacturer or "Hoymiles",
            model=source.model or "HIT xxL G3",
            sw_version=source.sw_version,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes from the native ESPHome entity."""
        await super().async_added_to_hass()
        if self._source_entity_id is None:
            return
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._source_entity_id],
                self._async_source_state_changed,
            )
        )

    @callback
    def _async_source_state_changed(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Forward source state changes immediately."""
        self.async_write_ha_state()

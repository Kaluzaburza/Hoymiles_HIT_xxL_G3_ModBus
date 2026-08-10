"""Localized Hoymiles sensor entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    EMS_PACKAGE_SENTINEL,
    EMS_PACKAGE_VERSION_ENTITY,
    VERSION,
)
from .entity import HoymilesProxyEntity
from .localization import localized_text_state
from .models import RuntimeData
from .rcm_sensor import HoymilesRCMOptimizerSensor
from .rce_sensor import HoymilesRCEOptimizerSensor
from .tariff_sensor import HoymilesTariffOptimizerSensor


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up localized sensors."""
    runtime: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HoymilesSensor(hass, entry, runtime, matched)
        for matched in runtime.entities["sensor"]
    ]
    entities.append(HoymilesRCEOptimizerSensor(hass, entry, runtime))
    entities.append(HoymilesTariffOptimizerSensor(hass, entry, runtime))
    entities.append(HoymilesRCMOptimizerSensor(hass, entry, runtime))
    entities.append(HoymilesSetupStatusSensor(hass, entry, runtime))
    async_add_entities(entities)


class HoymilesSetupStatusSensor(SensorEntity):
    """Summarize installation readiness and the next user action."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "setup_status"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: RuntimeData,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_setup_status"
        self._source_entity_ids = tuple(
            matched.source.entity_id
            for matches in runtime.entities.values()
            for matched in matches
            if matched.source is not None
        )
        self._expected_entity_count = sum(
            len(matches) for matches in runtime.entities.values()
        )

    @property
    def _language_is_polish(self) -> bool:
        return self.hass.config.language.casefold().startswith("pl")

    @property
    def _ems_loaded(self) -> bool:
        return self.hass.states.get(EMS_PACKAGE_SENTINEL) is not None

    @property
    def _ems_version(self) -> str | None:
        state = self.hass.states.get(EMS_PACKAGE_VERSION_ENTITY)
        return state.state if state is not None else None

    @property
    def _ems_restart_required(self) -> bool:
        return self._ems_loaded and self._ems_version != VERSION

    @property
    def _esp_online(self) -> bool:
        return any(
            (state := self.hass.states.get(entity_id)) is not None
            and state.state not in {STATE_UNAVAILABLE, STATE_UNKNOWN}
            for entity_id in self._source_entity_ids
        )

    @property
    def native_value(self) -> str:
        """Return a human-readable setup state."""
        if not self._esp_online:
            return "ESP32 niedostępne" if self._language_is_polish else "ESP32 offline"
        if len(self._source_entity_ids) < self._expected_entity_count:
            return (
                "Wymagana aktualizacja ESP32"
                if self._language_is_polish
                else "ESP32 update required"
            )
        if not self._ems_loaded:
            return (
                "Wymagane włączenie pakietów i restart"
                if self._language_is_polish
                else "Enable packages and restart"
            )
        if self._ems_restart_required:
            return (
                "Wymagany ponowny restart"
                if self._language_is_polish
                else "Restart required to finish update"
            )
        return "Gotowe" if self._language_is_polish else "Ready"

    @property
    def icon(self) -> str:
        """Return an icon matching readiness."""
        return "mdi:check-circle" if self.native_value in {"Gotowe", "Ready"} else "mdi:alert-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose a compact installation checklist."""
        source_count = len(self._source_entity_ids)
        coverage = (
            round(source_count / self._expected_entity_count * 100.0, 1)
            if self._expected_entity_count
            else 0.0
        )
        if not self._esp_online:
            next_step = (
                "Sprawdź zasilanie, Wi-Fi i integrację ESPHome."
                if self._language_is_polish
                else "Check power, Wi-Fi and the ESPHome integration."
            )
        elif source_count < self._expected_entity_count:
            next_step = (
                "Przebuduj i wgraj aktualny firmware ESPHome, następnie przeładuj integrację."
                if self._language_is_polish
                else "Build and upload the current ESPHome firmware, then reload the integration."
            )
        elif not self._ems_loaded:
            next_step = (
                "Włącz homeassistant: packages: !include_dir_named packages i uruchom HA ponownie."
                if self._language_is_polish
                else "Enable homeassistant: packages: !include_dir_named packages and restart HA."
            )
        elif self._ems_restart_required:
            next_step = (
                "Sprawdź konfigurację i uruchom Home Assistant ponownie jeszcze raz."
                if self._language_is_polish
                else "Validate the configuration and restart Home Assistant once more."
            )
        else:
            next_step = (
                "Instalacja jest gotowa."
                if self._language_is_polish
                else "The installation is ready."
            )
        return {
            "esp32_online": self._esp_online,
            "ems_package_loaded": self._ems_loaded,
            "ems_package_version": self._ems_version,
            "integration_version": VERSION,
            "restart_required": self._ems_restart_required,
            "source_entities": source_count,
            "expected_entities": self._expected_entity_count,
            "firmware_coverage_percent": coverage,
            "next_step": next_step,
        }

    async def async_added_to_hass(self) -> None:
        """Refresh readiness when ESPHome or the EMS package changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                (
                    *self._source_entity_ids,
                    EMS_PACKAGE_SENTINEL,
                    EMS_PACKAGE_VERSION_ENTITY,
                ),
                self._async_handle_state_change,
            )
        )

    @callback
    def _async_handle_state_change(self, _event: Event) -> None:
        self.async_write_ha_state()


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

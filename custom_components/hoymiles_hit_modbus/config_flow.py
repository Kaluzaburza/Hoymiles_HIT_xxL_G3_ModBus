"""Config flow for Hoymiles HIT xxL G3 Modbus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from .catalog import async_match_entities, matched_source_count
from .const import (
    CONF_COPY_ASSETS,
    CONF_RESOLVED_SOURCE_DEVICE_ID,
    CONF_SOURCE_DEVICE_ID,
    DOMAIN,
)
from .source_device import (
    configured_source_device_ids,
    linked_config_entry_ids,
)


class HoymilesHitModbusConfigFlow(
    ConfigFlow,
    domain=DOMAIN,
):
    """Configure localized entities for a Hoymiles ESPHome bridge."""

    VERSION = 1

    def _configured_source_device_ids(self) -> set[str]:
        """Return configured source anchors and verified split successors."""
        return configured_source_device_ids(
            self.hass.config_entries.async_entries(DOMAIN),
            CONF_SOURCE_DEVICE_ID,
            CONF_RESOLVED_SOURCE_DEVICE_ID,
        )

    async def _async_default_source_device_id(self) -> str | None:
        """Return the only unconfigured compatible ESPHome device, if unique."""
        configured = self._configured_source_device_ids()
        device_registry = dr.async_get(self.hass)
        candidates: list[str] = []
        for device in device_registry.devices.values():
            if device.id in configured:
                continue
            config_entries = (
                self.hass.config_entries.async_get_entry(entry_id)
                for entry_id in linked_config_entry_ids(device)
            )
            if not any(
                config_entry is not None and config_entry.domain == "esphome"
                for config_entry in config_entries
            ):
                continue
            _, matched = await async_match_entities(self.hass, device.id)
            if matched_source_count(matched) > 0:
                candidates.append(device.id)
                if len(candidates) > 1:
                    return None
        return candidates[0] if candidates else None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            source_device_id = user_input[CONF_SOURCE_DEVICE_ID]
            if source_device_id in self._configured_source_device_ids():
                return self.async_abort(reason="already_configured")
            source_device, matched = await async_match_entities(
                self.hass,
                source_device_id,
            )
            if source_device is None:
                errors["base"] = "device_not_found"
            elif matched_source_count(matched) == 0:
                errors["base"] = "no_entities"
            else:
                await self.async_set_unique_id(source_device_id)
                self._abort_if_unique_id_configured()
                title = (
                    source_device.name_by_user
                    or source_device.name
                    or "Hoymiles HIT xxL G3"
                )
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_SOURCE_DEVICE_ID: source_device_id,
                        CONF_COPY_ASSETS: True,
                    },
                )

        default_device_id = await self._async_default_source_device_id()
        source_field = (
            vol.Required(CONF_SOURCE_DEVICE_ID, default=default_device_id)
            if default_device_id is not None
            else vol.Required(CONF_SOURCE_DEVICE_ID)
        )
        data_schema = vol.Schema(
            {
                source_field: selector.DeviceSelector(
                    selector.DeviceSelectorConfig(integration="esphome")
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

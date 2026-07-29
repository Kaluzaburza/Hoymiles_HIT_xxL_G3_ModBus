"""Config flow for Hoymiles HIT xxL G3 Modbus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .catalog import async_match_entities, matched_source_count
from .const import CONF_COPY_ASSETS, CONF_SOURCE_DEVICE_ID, DOMAIN


class HoymilesHitModbusConfigFlow(
    ConfigFlow,
    domain=DOMAIN,
):
    """Configure localized entities for a Hoymiles ESPHome bridge."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            source_device_id = user_input[CONF_SOURCE_DEVICE_ID]
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
                return self.async_create_entry(title=title, data=user_input)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_DEVICE_ID): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(integration="esphome")
                ),
                vol.Optional(CONF_COPY_ASSETS, default=True): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

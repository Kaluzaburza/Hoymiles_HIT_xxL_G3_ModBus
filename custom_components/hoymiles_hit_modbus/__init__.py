"""Hoymiles HIT xxL G3 Modbus localized Home Assistant integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .assets import async_install_assets
from .catalog import async_match_entities
from .const import (
    ATTR_OVERWRITE,
    CONF_COPY_ASSETS,
    CONF_SOURCE_DEVICE_ID,
    DOMAIN,
    PLATFORMS,
    SERVICE_INSTALL_ASSETS,
)
from .models import RuntimeData


_LOGGER = logging.getLogger(__name__)

INSTALL_ASSETS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_OVERWRITE, default=False): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-wide services."""
    hass.data.setdefault(DOMAIN, {})

    async def async_handle_install_assets(call: ServiceCall) -> None:
        paths = await async_install_assets(
            hass,
            overwrite=call.data[ATTR_OVERWRITE],
        )
        _LOGGER.info(
            "Installed %s Hoymiles dashboard/automation assets: %s",
            len(paths),
            ", ".join(str(path) for path in paths),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_INSTALL_ASSETS,
        async_handle_install_assets,
        schema=INSTALL_ASSETS_SCHEMA,
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up a localized Hoymiles device from an ESPHome device."""
    source_device_id = entry.data[CONF_SOURCE_DEVICE_ID]
    source_device, matched = async_match_entities(hass, source_device_id)
    if source_device is None:
        _LOGGER.error("ESPHome source device %s no longer exists", source_device_id)
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = RuntimeData(
        source_device=source_device,
        entities=matched,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if entry.data.get(CONF_COPY_ASSETS, True):
        paths = await async_install_assets(hass, overwrite=False)
        if paths:
            _LOGGER.info(
                "Installed initial Hoymiles assets: %s",
                ", ".join(str(path) for path in paths),
            )
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload the integration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded

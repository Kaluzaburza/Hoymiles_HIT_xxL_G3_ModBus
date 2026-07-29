"""Hoymiles HIT xxL G3 Modbus localized Home Assistant integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .assets import RESOURCE_ROOT, async_install_assets
from .catalog import async_match_entities, matched_source_count
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
STATIC_URL = f"/api/{DOMAIN}/static"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

INSTALL_ASSETS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_OVERWRITE, default=False): cv.boolean,
    }
)


def _async_migrate_entity_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Migrate entity ids and unique ids to stable catalog identities."""
    entity_registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry,
        entry.entry_id,
    ):
        if (
            registry_entry.platform != DOMAIN
            or not registry_entry.translation_key
        ):
            continue

        desired_entity_id = (
            f"{registry_entry.domain}."
            f"hoymiles_hit_{registry_entry.translation_key}"
        )
        desired_unique_id = (
            f"{entry.entry_id}_{registry_entry.translation_key}"
        )
        entity_id_changed = registry_entry.entity_id != desired_entity_id
        unique_id_changed = registry_entry.unique_id != desired_unique_id
        if not entity_id_changed and not unique_id_changed:
            continue

        existing = entity_registry.async_get(desired_entity_id)
        if (
            entity_id_changed
            and existing is not None
            and existing.entity_id != registry_entry.entity_id
        ):
            _LOGGER.warning(
                "Cannot migrate %s to %s because the target already exists",
                registry_entry.entity_id,
                desired_entity_id,
            )
            continue

        old_entity_id = registry_entry.entity_id
        update: dict[str, str] = {}
        if entity_id_changed:
            update["new_entity_id"] = desired_entity_id
        if unique_id_changed:
            update["new_unique_id"] = desired_unique_id
        try:
            entity_registry.async_update_entity(old_entity_id, **update)
        except ValueError:
            _LOGGER.exception(
                "Cannot migrate registry identity for %s",
                old_entity_id,
            )
            continue
        _LOGGER.info(
            "Migrated Hoymiles registry identity from %s to %s",
            old_entity_id,
            desired_entity_id,
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-wide services."""
    hass.data.setdefault(DOMAIN, {})
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL,
                str(RESOURCE_ROOT / "www"),
                cache_headers=False,
            )
        ]
    )

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
    source_device, matched = await async_match_entities(hass, source_device_id)
    if source_device is None:
        _LOGGER.error("ESPHome source device %s no longer exists", source_device_id)
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = RuntimeData(
        source_device=source_device,
        entities=matched,
    )
    source_count = matched_source_count(matched)
    catalog_count = sum(len(entities) for entities in matched.values())
    if source_count < catalog_count:
        _LOGGER.warning(
            "%s of %s Hoymiles entities are present in ESPHome; "
            "the remaining proxies will stay unavailable until the "
            "ESPHome firmware is updated",
            source_count,
            catalog_count,
        )
    _async_migrate_entity_registry(hass, entry)
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

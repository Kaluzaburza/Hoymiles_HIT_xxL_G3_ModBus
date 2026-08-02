"""Constants for the Hoymiles HIT xxL G3 Modbus integration."""

from __future__ import annotations

from homeassistant.const import Platform


DOMAIN = "hoymiles_hit_modbus"
NAME = "Hoymiles HIT xxL G3 Modbus"
VERSION = "1.3.4"

CONF_SOURCE_DEVICE_ID = "source_device_id"
CONF_COPY_ASSETS = "copy_assets"

PLATFORMS: tuple[Platform, ...] = (
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
)

SUPPORTED_SOURCE_DOMAINS = {"button", "sensor", "number", "select"}

SERVICE_INSTALL_ASSETS = "install_assets"
ATTR_OVERWRITE = "overwrite"

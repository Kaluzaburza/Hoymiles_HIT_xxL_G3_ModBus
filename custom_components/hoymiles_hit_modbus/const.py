"""Constants for the Hoymiles HIT xxL G3 Modbus integration."""

from __future__ import annotations

from homeassistant.const import Platform


DOMAIN = "hoymiles_hit_modbus"
NAME = "Hoymiles HIT xxL G3 Modbus"
VERSION = "1.4.4"

# Existing helper created by the managed Home Assistant EMS package. Keep the
# setup-status sensor and Repairs check on this single shared sentinel so they
# cannot drift to a helper name that the package never creates.
EMS_PACKAGE_SENTINEL = "input_boolean.hoymiles_rce_discharge_enabled"

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

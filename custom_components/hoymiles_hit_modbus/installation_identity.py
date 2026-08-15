"""Persistent anonymous identity used only by support diagnostics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any
from uuid import RFC_4122, UUID, uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)

ANONYMOUS_INSTALLATION_ID = "anonymous_installation_id"
INSTALLATION_ID_SCHEMA_VERSION = 1
INSTALLATION_ID_SCHEMA_VERSION_KEY = "installation_id_schema_version"
INSTALLATION_ID_STORAGE_KEY = f"{DOMAIN}.installation_identity"
INSTALLATION_ID_STORAGE_VERSION = 1

_DATA_IDENTITY = f"{DOMAIN}_installation_identity"
_DATA_IDENTITY_LOCK = f"{DOMAIN}_installation_identity_lock"


class UnsupportedInstallationIdentitySchemaError(RuntimeError):
    """Raised when a newer identity schema needs an explicit migration."""


@dataclass(frozen=True, slots=True)
class InstallationIdentity:
    """Validated installation-wide identity exported in diagnostics."""

    anonymous_installation_id: str
    installation_id_schema_version: int

    def as_dict(self) -> dict[str, Any]:
        """Return the stable JSON/storage representation."""
        return {
            ANONYMOUS_INSTALLATION_ID: self.anonymous_installation_id,
            INSTALLATION_ID_SCHEMA_VERSION_KEY: (
                self.installation_id_schema_version
            ),
        }


def _canonical_uuid_v4(value: Any) -> str | None:
    """Return a canonical RFC 4122 UUID v4 string or None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        return None
    if (
        parsed.version != 4
        or parsed.variant != RFC_4122
        or str(parsed) != value
    ):
        return None
    return value


def _identity_from_storage(value: Any) -> InstallationIdentity | None:
    """Validate a persisted identity without deriving data from the host."""
    if not isinstance(value, dict):
        return None
    schema_version = value.get(INSTALLATION_ID_SCHEMA_VERSION_KEY)
    if (
        type(schema_version) is int
        and schema_version > INSTALLATION_ID_SCHEMA_VERSION
    ):
        raise UnsupportedInstallationIdentitySchemaError(
            "Stored anonymous installation identity uses unsupported schema "
            f"{schema_version}"
        )
    installation_id = _canonical_uuid_v4(
        value.get(ANONYMOUS_INSTALLATION_ID)
    )
    if (
        installation_id is None
        or type(schema_version) is not int
        or schema_version != INSTALLATION_ID_SCHEMA_VERSION
    ):
        return None
    return InstallationIdentity(installation_id, schema_version)


async def async_get_or_create_installation_identity(
    hass: HomeAssistant,
) -> InstallationIdentity:
    """Return the one persisted anonymous identity shared by all entries."""
    cached = hass.data.get(_DATA_IDENTITY)
    if isinstance(cached, InstallationIdentity):
        return cached

    lock = hass.data.setdefault(_DATA_IDENTITY_LOCK, asyncio.Lock())
    async with lock:
        cached = hass.data.get(_DATA_IDENTITY)
        if isinstance(cached, InstallationIdentity):
            return cached

        store: Store[dict[str, Any]] = Store(
            hass,
            INSTALLATION_ID_STORAGE_VERSION,
            INSTALLATION_ID_STORAGE_KEY,
        )
        stored = await store.async_load()
        identity = _identity_from_storage(stored)
        if identity is None:
            if stored is not None:
                _LOGGER.warning(
                    "Stored anonymous diagnostics identity is invalid; "
                    "generating a new random UUID"
                )
            identity = InstallationIdentity(
                anonymous_installation_id=str(uuid4()),
                installation_id_schema_version=(
                    INSTALLATION_ID_SCHEMA_VERSION
                ),
            )
            # Never publish/cache an identity that has not been persisted.
            await store.async_save(identity.as_dict())

        hass.data[_DATA_IDENTITY] = identity
        return identity

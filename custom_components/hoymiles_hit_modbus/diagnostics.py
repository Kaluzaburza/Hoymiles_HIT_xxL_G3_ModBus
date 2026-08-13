"""Diagnostics support for Hoymiles HIT xxL G3 Modbus."""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.components.recorder import history as recorder_history
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance as get_recorder_instance
from homeassistant.util import dt as dt_util

from .const import (
    CONF_RESOLVED_SOURCE_DEVICE_ID,
    CONF_SOURCE_DEVICE_ID,
    DOMAIN,
    VERSION,
)
from .diagnostic_redaction import REDACTED, sanitize_diagnostic_value
from .models import RuntimeData


REPORT_SCHEMA_VERSION = 1
HISTORY_HOURS = 24
MAX_HISTORY_EVENTS_PER_ENTITY = 500
ENTRY_REDACT_KEYS = {
    CONF_RESOLVED_SOURCE_DEVICE_ID,
    CONF_SOURCE_DEVICE_ID,
    "unique_id",
}
SNAPSHOT_DOMAINS = {
    "automation",
    "binary_sensor",
    "button",
    "input_boolean",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
    "number",
    "select",
    "sensor",
    "switch",
    "timer",
}
HISTORY_DOMAINS = {
    "automation",
    "binary_sensor",
    "input_boolean",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
    "select",
    "switch",
    "timer",
}
HISTORY_SENSOR_PARTS = (
    "alarm",
    "ems_working_mode",
    "inverter_status",
    "online_status",
    "rce_optimized_plan",
    "rcm_voltage_plan",
    "setup_status",
    "system_work_state",
    "tariff_charge_plan",
)


def _state_snapshot(state: State | None, *, key_hint: str = "") -> Any:
    """Return a compact, redacted representation of a Home Assistant state."""
    if state is None:
        return None
    state_value: Any = state.state
    if "serial" in key_hint.casefold():
        state_value = REDACTED
    return {
        "state": sanitize_diagnostic_value(state_value, key_hint=key_hint),
        "attributes": sanitize_diagnostic_value(dict(state.attributes)),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }


def _is_hoymiles_state(entity_id: str) -> bool:
    """Return whether an entity belongs to the managed Hoymiles setup."""
    domain, separator, object_id = entity_id.partition(".")
    return bool(
        separator
        and domain in SNAPSHOT_DOMAINS
        and (
            object_id.startswith("hoymiles_")
            or object_id.startswith("hoymiles_hit_")
        )
    )


def _needs_history(entity_id: str) -> bool:
    """Select slow-changing control entities without querying fast telemetry."""
    domain, _, object_id = entity_id.partition(".")
    if domain in HISTORY_DOMAINS:
        return True
    return domain == "sensor" and any(
        part in object_id for part in HISTORY_SENSOR_PARTS
    )


def _serialize_history_item(item: Any) -> dict[str, Any]:
    """Serialize recorder State objects and tolerate compressed dictionaries."""
    if isinstance(item, State):
        return {
            "state": sanitize_diagnostic_value(item.state),
            "last_changed": item.last_changed.isoformat(),
            "last_updated": item.last_updated.isoformat(),
        }
    if isinstance(item, dict):
        return sanitize_diagnostic_value(item)
    return {"value": sanitize_diagnostic_value(item)}


async def _async_history(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> dict[str, Any]:
    """Return 24 hours of significant control changes from Recorder."""
    if not entity_ids:
        return {"available": True, "hours": HISTORY_HOURS, "entities": {}}
    end = dt_util.utcnow()
    start = end - timedelta(hours=HISTORY_HOURS)
    try:
        query = partial(
            recorder_history.get_significant_states,
            hass,
            start,
            end,
            entity_ids,
            None,
            True,
            True,
            False,
            True,
        )
        raw = await get_recorder_instance(hass).async_add_executor_job(query)
    except Exception as err:  # noqa: BLE001 - diagnostics must still download
        return {
            "available": False,
            "hours": HISTORY_HOURS,
            "error_type": type(err).__name__,
        }

    serialized: dict[str, list[dict[str, Any]]] = {}
    truncated: list[str] = []
    for entity_id in entity_ids:
        items = list(raw.get(entity_id, []))
        if len(items) > MAX_HISTORY_EVENTS_PER_ENTITY:
            items = items[-MAX_HISTORY_EVENTS_PER_ENTITY:]
            truncated.append(entity_id)
        serialized[entity_id] = [_serialize_history_item(item) for item in items]
    return {
        "available": True,
        "hours": HISTORY_HOURS,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "entities": serialized,
        "truncated_entities": truncated,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return a privacy-safe report downloadable from Home Assistant."""
    runtime: RuntimeData | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    entity_registry = er.async_get(hass)
    proxy_entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    proxies_by_key = {
        (registry_entry.domain, registry_entry.translation_key): registry_entry
        for registry_entry in proxy_entries
        if registry_entry.translation_key
    }

    catalog_rows: list[dict[str, Any]] = []
    expected_by_domain: dict[str, int] = {}
    present_by_domain: dict[str, int] = {}
    missing_translation_keys: list[str] = []
    if runtime is not None:
        for domain, matched_entities in runtime.entities.items():
            expected_by_domain[domain] = len(matched_entities)
            present_by_domain[domain] = sum(
                matched.source is not None for matched in matched_entities
            )
            for matched in matched_entities:
                translation_key = str(matched.catalog["translation_key"])
                proxy = proxies_by_key.get((domain, translation_key))
                source_state = (
                    hass.states.get(matched.source.entity_id)
                    if matched.source is not None
                    else None
                )
                if matched.source is None:
                    missing_translation_keys.append(translation_key)
                catalog_rows.append(
                    {
                        "domain": domain,
                        "translation_key": translation_key,
                        "source_present": matched.source is not None,
                        "source_state": _state_snapshot(
                            source_state,
                            key_hint=translation_key,
                        ),
                        "proxy_entity_id": proxy.entity_id if proxy else None,
                        "proxy_state": _state_snapshot(
                            hass.states.get(proxy.entity_id) if proxy else None,
                            key_hint=translation_key,
                        ),
                    }
                )

    managed_states = sorted(
        (
            state
            for state in hass.states.async_all()
            if _is_hoymiles_state(state.entity_id)
        ),
        key=lambda state: state.entity_id,
    )
    state_snapshot = {
        state.entity_id: _state_snapshot(state, key_hint=state.entity_id)
        for state in managed_states
    }
    history_entity_ids = [
        state.entity_id for state in managed_states if _needs_history(state.entity_id)
    ]

    source_device: dict[str, Any] | None = None
    if runtime is not None:
        source_device = {
            "manufacturer": sanitize_diagnostic_value(
                getattr(runtime.source_device, "manufacturer", None)
            ),
            "model": sanitize_diagnostic_value(
                getattr(runtime.source_device, "model", None)
            ),
            "hardware_version": sanitize_diagnostic_value(
                getattr(runtime.source_device, "hw_version", None)
            ),
            "firmware_version": sanitize_diagnostic_value(
                getattr(runtime.source_device, "sw_version", None)
            ),
        }

    return {
        "report_schema": REPORT_SCHEMA_VERSION,
        "generated_at": dt_util.utcnow().isoformat(),
        "integration_version": VERSION,
        "config_entry": {
            "title": "Hoymiles HIT xxL G3 Modbus",
            "data": async_redact_data(dict(entry.data), ENTRY_REDACT_KEYS),
            "options": async_redact_data(dict(entry.options), ENTRY_REDACT_KEYS),
        },
        "source_device": source_device,
        "catalog_coverage": {
            "runtime_loaded": runtime is not None,
            "expected_by_domain": expected_by_domain,
            "present_by_domain": present_by_domain,
            "missing_count": len(missing_translation_keys),
            "missing_translation_keys": sorted(missing_translation_keys),
        },
        "catalog_entities": catalog_rows,
        "managed_state_snapshot": state_snapshot,
        "control_history": await _async_history(hass, history_entity_ids),
    }

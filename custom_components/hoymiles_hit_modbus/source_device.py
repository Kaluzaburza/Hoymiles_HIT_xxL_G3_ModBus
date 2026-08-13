"""Resolve ESPHome source devices across Home Assistant registry splits."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


ESPHOME_DOMAIN = "esphome"

MatchedEntities = dict[str, list[Any]]
MatchEntities = Callable[
    [Any, str],
    Awaitable[tuple[Any | None, MatchedEntities]],
]
EntriesForDevice = Callable[[Any, str, bool], list[Any]]


@dataclass(frozen=True, slots=True)
class SourceDeviceResolution:
    """A source-device lookup result and its migration evidence."""

    source_device: Any | None
    matched: MatchedEntities
    resolved_device_id: str | None
    rebound: bool
    exact_successor_count: int = 0
    compatible_successor_count: int = 0


def resolved_source_entry_data_update(
    data: Mapping[str, Any],
    resolution: SourceDeviceResolution,
    resolved_device_key: str,
) -> dict[str, Any] | None:
    """Return an entry-data update only for a newly verified successor."""
    resolved_device_id = resolution.resolved_device_id
    if (
        not resolution.rebound
        or resolution.source_device is None
        or resolved_device_id is None
        or data.get(resolved_device_key) == resolved_device_id
    ):
        return None
    updated_data = dict(data)
    updated_data[resolved_device_key] = resolved_device_id
    return updated_data


def persist_resolved_source_entry(
    hass: Any,
    entry: Any,
    resolution: SourceDeviceResolution,
    resolved_device_key: str,
) -> bool:
    """Persist a verified successor without replacing its composite anchor."""
    updated_data = resolved_source_entry_data_update(
        entry.data,
        resolution,
        resolved_device_key,
    )
    if updated_data is None:
        return False
    hass.config_entries.async_update_entry(entry, data=updated_data)
    return True


def configured_source_device_ids(
    entries: Iterable[Any],
    source_device_key: str,
    resolved_device_key: str,
) -> set[str]:
    """Return both configured source anchors and verified live successors."""
    return {
        str(device_id)
        for entry in entries
        for key in (source_device_key, resolved_device_key)
        if (device_id := entry.data.get(key)) is not None
    }


def linked_config_entry_ids(device: Any) -> set[str]:
    """Return the device's owning config entries across HA registry versions."""
    if (config_entry_id := getattr(device, "config_entry_id", None)) is not None:
        return {str(config_entry_id)}
    return {
        str(entry_id)
        for entry_id in (getattr(device, "config_entries", ()) or ())
        if entry_id is not None
    }


def _identifiers(device: Any | None) -> set[tuple[str, str]]:
    """Return normalized device identifiers when the registry provides them."""
    if device is None:
        return set()
    return {
        (str(domain), str(value))
        for domain, value in (getattr(device, "identifiers", ()) or ())
    }


def _matched_sources(matched: MatchedEntities) -> list[Any]:
    """Return native entities that actually back catalog records."""
    return [
        source
        for entities in matched.values()
        for matched_entity in entities
        if (source := getattr(matched_entity, "source", None)) is not None
    ]


def _esphome_owner_entry_id(hass: Any, candidate: Any) -> str | None:
    """Return the sole ESPHome config entry owning a split device."""
    linked_entry_ids = linked_config_entry_ids(candidate)
    if len(linked_entry_ids) != 1:
        return None
    config_entry_id = next(iter(linked_entry_ids))
    config_entry = hass.config_entries.async_get_entry(config_entry_id)
    if config_entry is None or config_entry.domain != ESPHOME_DOMAIN:
        return None
    return config_entry_id


async def _async_resolve_source_device(
    hass: Any,
    source_device_id: str,
    match_entities: MatchEntities,
    device_registry: Any,
    entity_registry: Any,
    entries_for_device: EntriesForDevice,
    previously_resolved_device_id: str | None = None,
) -> SourceDeviceResolution:
    """Resolve a live source or one unambiguous ESPHome composite successor."""
    source_device, matched = await match_entities(hass, source_device_id)

    # Membership is intentional: DeviceRegistry.async_get() can synthesize a
    # read-only composite for an id that no longer denotes a live device.
    if source_device_id in device_registry.devices:
        return SourceDeviceResolution(
            source_device=source_device,
            matched=matched,
            resolved_device_id=source_device_id if source_device is not None else None,
            rebound=False,
        )

    anchor_identifiers = _identifiers(source_device)
    exact_successors = [
        device
        for device in device_registry.devices.values()
        if getattr(device, "composite_device_id", None) == source_device_id
    ]
    exact_successor_ids = {device.id for device in exact_successors}
    candidates = list(exact_successors)
    if (
        previously_resolved_device_id is not None
        and (
            previous_candidate := device_registry.devices.get(
                previously_resolved_device_id
            )
        )
        is not None
        and all(
            candidate.id != previous_candidate.id for candidate in candidates
        )
    ):
        # DeletedDeviceEntry does not retain composite_device_id in HA 2026.8.
        # A remove/re-add can therefore lose the exact anchor link while another
        # non-ESPHome split still retains it. Reconsider the last verified live
        # id, deduplicate it against exact successors, and run every candidate
        # through all ESPHome ownership/entity checks below.
        candidates.append(previous_candidate)
    compatible: list[tuple[Any, MatchedEntities]] = []

    for candidate in candidates:
        owner_entry_id = _esphome_owner_entry_id(hass, candidate)
        if owner_entry_id is None:
            continue

        # Identifiers are supplementary evidence. Empty identifiers are valid
        # for ESPHome after the 2026.8 split, but contradictory non-empty sets
        # must never be accepted.
        candidate_identifiers = _identifiers(candidate)
        if (
            candidate.id in exact_successor_ids
            and anchor_identifiers
            and candidate_identifiers
            and anchor_identifiers.isdisjoint(candidate_identifiers)
        ):
            continue

        native_entries = [
            entity
            for entity in entries_for_device(
                entity_registry,
                candidate.id,
                True,
            )
            if getattr(entity, "platform", None) == ESPHOME_DOMAIN
            and getattr(entity, "config_entry_id", None) == owner_entry_id
        ]
        if not native_entries:
            continue
        native_entity_ids = {
            getattr(entity, "entity_id", None) for entity in native_entries
        }

        resolved_device, candidate_matched = await match_entities(
            hass,
            candidate.id,
        )
        matched_sources = _matched_sources(candidate_matched)
        if (
            resolved_device is None
            or getattr(resolved_device, "id", None) != candidate.id
            or not matched_sources
        ):
            continue
        if any(
            getattr(entity, "device_id", None) != candidate.id
            or getattr(entity, "platform", None) != ESPHOME_DOMAIN
            or getattr(entity, "config_entry_id", None) != owner_entry_id
            or getattr(entity, "entity_id", None) not in native_entity_ids
            for entity in matched_sources
        ):
            continue
        compatible.append((resolved_device, candidate_matched))

    if len(compatible) != 1:
        return SourceDeviceResolution(
            source_device=None,
            matched=matched,
            resolved_device_id=None,
            rebound=False,
            exact_successor_count=len(exact_successors),
            compatible_successor_count=len(compatible),
        )

    resolved_device, resolved_matched = compatible[0]
    return SourceDeviceResolution(
        source_device=resolved_device,
        matched=resolved_matched,
        resolved_device_id=resolved_device.id,
        rebound=True,
        exact_successor_count=len(exact_successors),
        compatible_successor_count=1,
    )


async def async_resolve_source_device(
    hass: Any,
    source_device_id: str,
    match_entities: MatchEntities,
    previously_resolved_device_id: str | None = None,
) -> SourceDeviceResolution:
    """Resolve the configured ESPHome source with fail-closed split recovery."""
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    return await _async_resolve_source_device(
        hass,
        source_device_id,
        match_entities,
        dr.async_get(hass),
        er.async_get(hass),
        er.async_entries_for_device,
        previously_resolved_device_id,
    )

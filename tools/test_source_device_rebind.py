"""Regression tests for fail-closed ESPHome source-device rebinding."""

from __future__ import annotations

import asyncio
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = (
    ROOT
    / "custom_components"
    / "hoymiles_hit_modbus"
    / "source_device.py"
)
CONFIG_FLOW_PATH = (
    ROOT
    / "custom_components"
    / "hoymiles_hit_modbus"
    / "config_flow.py"
)
MODULE_NAME = "hoymiles_source_device_contract"


def _load_source_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SOURCE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


SOURCE = _load_source_module()
RESOLVED_KEY = "resolved_source_device_id"


def _device(
    device_id: str,
    config_entry_id: str,
    *,
    composite_device_id: str | None = None,
    identifiers: set[tuple[str, str]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=device_id,
        config_entry_id=config_entry_id,
        composite_device_id=composite_device_id,
        identifiers=identifiers or set(),
    )


def _entity(
    entity_id: str,
    device_id: str,
    config_entry_id: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        entity_id=entity_id,
        device_id=device_id,
        config_entry_id=config_entry_id,
        platform="esphome",
    )


class FakeConfigEntries:
    """Minimal config-entry lookup used by the resolver."""

    def __init__(self, domains: dict[str, str]) -> None:
        self._entries = {
            entry_id: SimpleNamespace(entry_id=entry_id, domain=domain)
            for entry_id, domain in domains.items()
        }

    def async_get_entry(self, entry_id: str) -> SimpleNamespace | None:
        return self._entries.get(entry_id)


def _fixture(
    devices: list[SimpleNamespace],
    entities: dict[str, list[SimpleNamespace]],
    domains: dict[str, str],
    *,
    synthetic_anchor: SimpleNamespace | None = None,
) -> tuple[Any, Any, Any, Any, Any]:
    device_by_id = {device.id: device for device in devices}
    hass = SimpleNamespace(config_entries=FakeConfigEntries(domains))
    device_registry = SimpleNamespace(devices=device_by_id)
    entity_registry = SimpleNamespace(entities=entities)

    def entries_for_device(
        registry: SimpleNamespace,
        device_id: str,
        include_disabled_entities: bool,
    ) -> list[SimpleNamespace]:
        assert include_disabled_entities
        return list(registry.entities.get(device_id, ()))

    async def match_entities(
        _hass: Any,
        device_id: str,
    ) -> tuple[SimpleNamespace | None, dict[str, list[SimpleNamespace]]]:
        device = device_by_id.get(device_id)
        if device is None:
            return synthetic_anchor, {"sensor": []}
        matched = {
            "sensor": [
                SimpleNamespace(source=entity)
                for entity in entities.get(device_id, ())
            ]
        }
        return device, matched

    return (
        hass,
        device_registry,
        entity_registry,
        entries_for_device,
        match_entities,
    )


async def _resolve(
    source_device_id: str,
    fixture: tuple[Any, Any, Any, Any, Any],
    previously_resolved_device_id: str | None = None,
) -> Any:
    (
        hass,
        device_registry,
        entity_registry,
        entries_for_device,
        match_entities,
    ) = fixture
    return await SOURCE._async_resolve_source_device(
        hass,
        source_device_id,
        match_entities,
        device_registry,
        entity_registry,
        entries_for_device,
        previously_resolved_device_id,
    )


async def _test_missing_source_unique_successor() -> None:
    old_id = "old-composite"
    successor = _device(
        "new-esphome",
        "esphome-entry",
        composite_device_id=old_id,
        # Empty identifiers are the live HA 2026.8 ESPHome case.
        identifiers=set(),
    )
    source_entity = _entity(
        "sensor.hoymiles_active_power",
        successor.id,
        successor.config_entry_id,
    )
    fixture = _fixture(
        [successor],
        {successor.id: [source_entity]},
        {successor.config_entry_id: "esphome"},
        synthetic_anchor=SimpleNamespace(
            id=old_id,
            identifiers={("esphome", "physical-inverter")},
        ),
    )

    resolution = await _resolve(old_id, fixture)
    assert resolution.source_device is successor
    assert resolution.resolved_device_id == successor.id
    assert resolution.rebound
    assert resolution.exact_successor_count == 1
    assert resolution.compatible_successor_count == 1

    original_data = {"source_device_id": old_id, "copy_assets": True}
    entry = SimpleNamespace(data=original_data)

    class RecordingConfigEntries:
        def __init__(self) -> None:
            self.updates: list[tuple[Any, dict[str, Any]]] = []

        def async_update_entry(self, target: Any, *, data: dict[str, Any]) -> None:
            self.updates.append((target, data))

    config_entries = RecordingConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    assert SOURCE.persist_resolved_source_entry(
        hass,
        entry,
        resolution,
        RESOLVED_KEY,
    )
    assert len(config_entries.updates) == 1
    updated_entry, updated = config_entries.updates[0]
    assert updated_entry is entry
    assert updated == {
        **original_data,
        RESOLVED_KEY: successor.id,
    }
    # The stable composite anchor must survive the migration.
    assert updated["source_device_id"] == old_id
    assert RESOLVED_KEY not in original_data


async def _test_previous_verified_successor_is_revalidated() -> None:
    old_id = "old-composite"
    successor = _device("new-esphome", "esphome-entry")
    retained_non_esphome_split = _device(
        "localized-helper",
        "helper-entry",
        composite_device_id=old_id,
        identifiers={("hoymiles_hit_modbus", "localized-helper")},
    )
    successor.identifiers = {("esphome", "restored-inverter")}
    source_entity = _entity(
        "sensor.hoymiles_active_power",
        successor.id,
        successor.config_entry_id,
    )
    fixture = _fixture(
        [retained_non_esphome_split, successor],
        {
            retained_non_esphome_split.id: [],
            successor.id: [source_entity],
        },
        {
            retained_non_esphome_split.config_entry_id: "hoymiles_hit_modbus",
            successor.config_entry_id: "esphome",
        },
        synthetic_anchor=SimpleNamespace(
            id=old_id,
            identifiers={("hoymiles_hit_modbus", "localized-helper")},
        ),
    )

    resolution = await _resolve(old_id, fixture, successor.id)
    assert resolution.source_device is successor
    assert resolution.resolved_device_id == successor.id
    assert resolution.rebound
    assert resolution.exact_successor_count == 1
    assert resolution.compatible_successor_count == 1

    # The saved id is evidence, not authority: a non-ESPHome replacement with
    # the same id must be rejected after full ownership validation.
    wrong_fixture = _fixture(
        [successor],
        {successor.id: [source_entity]},
        {successor.config_entry_id: "mqtt"},
    )
    rejected = await _resolve(old_id, wrong_fixture, successor.id)
    assert rejected.source_device is None
    assert rejected.compatible_successor_count == 0


async def _test_multiple_successors_fail_closed() -> None:
    old_id = "old-composite"
    first = _device(
        "new-esphome-a",
        "esphome-entry-a",
        composite_device_id=old_id,
    )
    second = _device(
        "new-esphome-b",
        "esphome-entry-b",
        composite_device_id=old_id,
    )
    first_entity = _entity("sensor.hoymiles_a", first.id, first.config_entry_id)
    second_entity = _entity("sensor.hoymiles_b", second.id, second.config_entry_id)
    fixture = _fixture(
        [first, second],
        {first.id: [first_entity], second.id: [second_entity]},
        {
            first.config_entry_id: "esphome",
            second.config_entry_id: "esphome",
        },
    )

    resolution = await _resolve(old_id, fixture)
    assert resolution.source_device is None
    assert resolution.resolved_device_id is None
    assert not resolution.rebound
    assert resolution.exact_successor_count == 2
    assert resolution.compatible_successor_count == 2
    assert (
        SOURCE.resolved_source_entry_data_update(
            {"source_device_id": old_id},
            resolution,
            RESOLVED_KEY,
        )
        is None
    )


async def _test_existing_source_is_unchanged() -> None:
    source = _device("existing-source", "esphome-entry")
    source_entity = _entity(
        "sensor.hoymiles_active_power",
        source.id,
        source.config_entry_id,
    )
    fixture = _fixture(
        [source],
        {source.id: [source_entity]},
        {source.config_entry_id: "esphome"},
    )

    resolution = await _resolve(source.id, fixture)
    assert resolution.source_device is source
    assert resolution.resolved_device_id == source.id
    assert not resolution.rebound
    assert resolution.exact_successor_count == 0
    assert resolution.compatible_successor_count == 0
    assert (
        SOURCE.resolved_source_entry_data_update(
            {"source_device_id": source.id},
            resolution,
            RESOLVED_KEY,
        )
        is None
    )


def _test_config_flow_rejects_resolved_successor_duplicate() -> None:
    entries = [
        SimpleNamespace(
            data={
                "source_device_id": "old-composite",
                RESOLVED_KEY: "new-esphome",
            }
        ),
        SimpleNamespace(data={"source_device_id": "second-inverter"}),
    ]
    configured = SOURCE.configured_source_device_ids(
        entries,
        "source_device_id",
        RESOLVED_KEY,
    )
    assert configured == {
        "old-composite",
        "new-esphome",
        "second-inverter",
    }

    tree = ast.parse(
        CONFIG_FLOW_PATH.read_text(encoding="utf-8"),
        filename=str(CONFIG_FLOW_PATH),
    )
    flow_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "HoymilesHitModbusConfigFlow"
    )
    methods = {
        node.name: node
        for node in flow_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    default_method = methods["_async_default_source_device_id"]
    user_method = methods["async_step_user"]

    for method in (default_method, user_method):
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_configured_source_device_ids"
            for node in ast.walk(method)
        ), f"{method.name} does not apply the configured successor guard"

    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "async_abort"
        and any(
            keyword.arg == "reason"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "already_configured"
            for keyword in node.keywords
        )
        for node in ast.walk(user_method)
    ), "manual source selection does not abort a resolved successor duplicate"


async def _test_wrong_linkage_and_entity_evidence_are_rejected() -> None:
    old_id = "old-composite"
    wrong_owner = _device(
        "not-esphome-owned",
        "other-entry",
        composite_device_id=old_id,
    )
    wrong_device = _device(
        "esphome-candidate",
        "esphome-entry",
        composite_device_id=old_id,
    )
    misplaced_entity = _entity(
        "sensor.hoymiles_active_power",
        "different-device",
        wrong_device.config_entry_id,
    )
    fixture = _fixture(
        [wrong_owner, wrong_device],
        {
            wrong_owner.id: [
                _entity(
                    "sensor.not_esphome",
                    wrong_owner.id,
                    wrong_owner.config_entry_id,
                )
            ],
            wrong_device.id: [misplaced_entity],
        },
        {
            wrong_owner.config_entry_id: "mqtt",
            wrong_device.config_entry_id: "esphome",
        },
    )

    resolution = await _resolve(old_id, fixture)
    assert resolution.source_device is None
    assert resolution.exact_successor_count == 2
    assert resolution.compatible_successor_count == 0


async def main() -> None:
    """Run source-device rebinding regression tests."""
    await _test_missing_source_unique_successor()
    await _test_previous_verified_successor_is_revalidated()
    await _test_multiple_successors_fail_closed()
    await _test_existing_source_is_unchanged()
    _test_config_flow_rejects_resolved_successor_duplicate()
    await _test_wrong_linkage_and_entity_evidence_are_rejected()
    print("Source-device rebind tests: 6/6 PASS")


if __name__ == "__main__":
    asyncio.run(main())

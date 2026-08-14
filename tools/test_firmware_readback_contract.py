#!/usr/bin/env python3
"""Source-contract checks for physical FC03 actuator acknowledgements."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def platform_block(source: str, component_id: str) -> str:
    marker = f"    id: {component_id}\n"
    start = source.index(marker)
    block_start = source.rfind("  - platform:", 0, start)
    assert block_start >= 0, component_id
    block_end = source.find("\n  - platform:", start)
    return source[block_start:] if block_end < 0 else source[block_start:block_end]


def script_block(source: str, component_id: str) -> str:
    marker = f"  - id: {component_id}\n"
    start = source.index(marker)
    block_end = source.find("\nsensor:\n", start)
    assert block_end >= 0, component_id
    return source[start:block_end]


def main() -> None:
    settings = (ROOT / "packages" / "settings.yaml").read_text(encoding="utf-8")
    parallel = (ROOT / "packages" / "parallel_network.yaml").read_text(
        encoding="utf-8"
    )
    overview = (ROOT / "packages" / "overview.yaml").read_text(encoding="utf-8")
    battery = (ROOT / "packages" / "battery.yaml").read_text(encoding="utf-8")
    pv = (ROOT / "packages" / "pv.yaml").read_text(encoding="utf-8")
    meters = (ROOT / "packages" / "meters.yaml").read_text(encoding="utf-8")
    load = (ROOT / "packages" / "backup_load.yaml").read_text(encoding="utf-8")
    catalog = json.loads(
        (ROOT / "custom_components" / "hoymiles_hit_modbus" / "entity_catalog.json")
        .read_text(encoding="utf-8")
    )

    readbacks = {
        "ems_mode_raw_4300": (4300, "U_WORD"),
        "self_used_soc_readback_4301": (4301, "U_WORD"),
        "backup_soc_raw_4302": (4302, "U_WORD"),
        "force_charge_soc_readback_4303": (4303, "U_WORD"),
        "maximum_charge_power_readback_4304": (4304, "U_WORD"),
        "force_discharge_soc_readback_4305": (4305, "U_WORD"),
        "maximum_discharge_power_readback_4306": (4306, "U_WORD"),
        "gcf_enable_readback_258": (258, "U_WORD"),
        "gcf_export_soft_limit_ratio_readback_259": (259, "S_WORD"),
        "battery_max_charge_power_readback_306": (306, "U_WORD"),
    }
    for component_id, (address, value_type) in readbacks.items():
        block = platform_block(settings, component_id)
        assert "platform: modbus_controller" in block, component_id
        assert "register_type: holding" in block, component_id
        assert f"address: {address}" in block, component_id
        assert f"value_type: {value_type}" in block, component_id
        assert "force_update: true" in block, component_id
        assert "internal: true" not in block, component_id

    generations = {
        "ems_control_readback_generation": "maximum_discharge_power_readback_4306",
        "gcf_control_readback_generation": "gcf_export_soft_limit_ratio_readback_259",
        "battery_charge_power_readback_generation": (
            "battery_max_charge_power_readback_306"
        ),
    }
    for generation_id, poll_sensor_id in generations.items():
        generation_block = platform_block(settings, generation_id)
        poll_block = platform_block(settings, poll_sensor_id)
        assert "platform: template" in generation_block, generation_id
        assert "update_interval: never" in generation_block, generation_id
        assert "force_update: true" in generation_block, generation_id
        publish = f"id({generation_id}).publish_state(generation);"
        assert settings.count(publish) == 1, generation_id
        assert publish in poll_block, generation_id

    mode_block = platform_block(settings, "ems_mode_4300")
    ems_writer = script_block(settings, "ems_write_complete_block_4300_4306")
    assert "update_interval: never" in mode_block
    assert "optimistic:" not in mode_block
    assert ".publish_state(x)" not in mode_block
    assert "id(ems_write_complete_block_4300_4306)->execute(" in mode_block
    assert "create_write_multiple_command" not in mode_block
    assert "send_raw" not in mode_block

    # Every EMS edit is composed from the complete physical 4300-4306
    # snapshot. A Master gets the same FC16 frame on the system broadcast
    # address 0; a single inverter keeps the addressed controller path.
    assert "mode: queued" in ems_writer
    assert "id(ems_verified_hardware_readback_supported).state" in ems_writer
    assert ems_writer.index("ems_verified_hardware_readback_supported") < ems_writer.index(
        "send_raw(payload)"
    )
    assert "static_cast<uint32_t>(millis() - ems_snapshot_ms) > 15000U" in ems_writer
    assert "if (machine_type == 1)" in ems_writer
    assert "0x00, 0x10, 0x10, 0xCC, 0x00, 0x07, 0x0E" in ems_writer
    assert "id(modbus_1).send_raw(payload);" in ems_writer
    assert "create_write_multiple_command" in ems_writer
    assert "controller, 4300, values.size(), values" in ems_writer
    assert ".publish_state(" not in ems_writer
    ems_last_poll = platform_block(settings, "maximum_discharge_power_readback_4306")
    assert "id(ems_control_last_readback_ms) = millis();" in ems_last_poll
    assert "id(ems_control_last_readback_ms) = millis();" not in platform_block(
        settings, "ems_mode_raw_4300"
    )
    for mirror_id in (
        "self_used_soc_readback_4301",
        "backup_soc_raw_4302",
        "force_charge_soc_readback_4303",
        "maximum_charge_power_readback_4304",
        "force_discharge_soc_readback_4305",
        "maximum_discharge_power_readback_4306",
    ):
        assert f"id({mirror_id}).state" in mode_block, mirror_id
    for range_marker in (
        "valid_range(self_use_soc, 10.0f, 100.0f)",
        "valid_range(backup_soc, 60.0f, 100.0f)",
        "valid_range(force_charge_soc, 10.0f, 100.0f)",
        "valid_range(maximum_charge_power, 0.0f, 100.0f)",
        "valid_range(force_discharge_soc, 0.0f, 100.0f)",
        "valid_range(maximum_discharge_power, 0.0f, 100.0f)",
    ):
        assert range_marker in ems_writer, range_marker
    assert "std::isfinite(value)" in ems_writer
    assert "encode(id(self_used_soc_4301).state" not in mode_block
    assert "encode(id(force_charge_soc_4303).state" not in mode_block
    assert "encode(id(maximum_charge_power_4304).state" not in mode_block
    assert "encode(id(force_discharge_soc_4305).state" not in mode_block
    assert "encode(id(maximum_discharge_power_4306).state" not in mode_block

    ems_actuators = (
        "self_used_soc_4301",
        "force_charge_soc_4303",
        "maximum_charge_power_4304",
        "force_discharge_soc_4305",
        "maximum_discharge_power_4306",
    )
    for component_id in ems_actuators:
        block = platform_block(settings, component_id)
        assert "write_lambda:" in block, component_id
        assert "id(ems_verified_hardware_readback_supported).has_state()" in block, component_id
        assert "id(ems_verified_hardware_readback_supported).state" in block, component_id
        assert ".state < 0.5f" in block, component_id
        assert "id(ems_write_complete_block_4300_4306)->execute(" in block, component_id
        assert "return {};" in block, component_id
        assert "readback_generation).publish_state" not in block, component_id

    direct_actuators = (
        "gcf_enable_258",
        "gcf_export_soft_limit_ratio_259",
        "battery_max_charge_power_306",
    )
    for component_id in direct_actuators:
        block = platform_block(settings, component_id)
        assert "write_lambda:" in block, component_id
        assert (
            "id(direct_register_verified_readback_supported).has_state()" in block
        ), component_id
        assert "id(direct_register_verified_readback_supported).state" in block, component_id
        assert ".state < 0.5f" in block, component_id
        assert "ems_write_complete_block_4300_4306" not in block, component_id

    # A ModbusNumber/ModbusSelect command echo is not a physical acknowledgement.
    number_source = settings.split("\nnumber:\n", 1)[1]
    assert "readback_generation).publish_state" not in number_source

    for component_id, address in (
        ("machines_type_6048", 6048),
        ("number_of_machines_master_and_slave_6049", 6049),
    ):
        block = platform_block(parallel, component_id)
        assert "modbus_controller_id: ${modbus_settings_controller_id}" in block
        assert f"address: {address}" in block
        assert "skip_updates:" not in block
        assert "force_update: true" in block

    topology_generation = platform_block(
        parallel, "parallel_topology_readback_generation"
    )
    topology_first = platform_block(parallel, "machines_type_6048")
    topology_poll = platform_block(parallel, "number_of_machines_master_and_slave_6049")
    assert "update_interval: never" in topology_generation
    assert "force_update: true" in topology_generation
    assert "id(parallel_topology_last_readback_ms) = millis();" in topology_poll
    assert (
        "id(parallel_topology_readback_generation).publish_state(generation);"
        in topology_poll
    )
    assert "parallel_topology_last_readback_ms" not in topology_first
    assert "parallel_topology_readback_generation).publish_state" not in topology_first

    capability = platform_block(parallel, "ems_verified_hardware_readback_supported")
    assert 'name: "EMS Verified Hardware Readback Supported"' in capability
    assert "accuracy_decimals: 0" in capability
    assert "update_interval: 1s" in capability
    assert "static_cast<uint32_t>(millis() - last_readback) > 60000U" in capability
    assert "if (machine_type == 0) return 1.0f;" in capability
    assert "machine_type != 1" in capability
    assert "return count >= 2 && count <= 10 ? 1.0f : 0.0f;" in capability
    assert "communication_address_" not in capability

    direct_capability = platform_block(
        parallel, "direct_register_verified_readback_supported"
    )
    assert 'name: "Direct Register Verified Readback Supported"' in direct_capability
    assert "static_cast<uint32_t>(millis() - last_readback) > 60000U" in direct_capability
    assert "return machine_type == 0 ? 1.0f : 0.0f;" in direct_capability
    assert "\nbinary_sensor:\n" not in parallel
    assert "Gotowe - broadcast EMS, odczyt Mastera" in parallel

    # The broadcast itself has no Modbus response. HA must therefore certify
    # completion only from the later physical Master FC03 generation.
    assert settings.count("send_raw(payload)") == 1
    assert settings.count("0x00, 0x10, 0x10, 0xCC") == 1

    # Operational overview timestamps must prove a physical FC03 response.
    # A periodic template may keep publishing its cached value after Modbus
    # goes silent and defeats every HA-side max-age safety gate.
    overview_sources = {
        "pv_total_power_30001": (
            (overview, "pv_total_power_master_30001", "!= 1"),
            (pv, "pv_total_power_8528", "== 1"),
        ),
        "grid_total_active_power_30011": (
            (overview, "grid_total_active_power_master_30011", "!= 1"),
            (meters, "grid_total_active_power_1814", "== 1"),
        ),
        "load_active_power_30015": (
            (overview, "load_active_power_master_30015", "!= 1"),
            (load, "load_power_total_8553", "== 1"),
        ),
        "battery_soc_30020": (
            (overview, "battery_soc_master_30020", "!= 1"),
            (battery, "battery_soc_1909", "== 1"),
        ),
    }
    public_poll_entities = tuple(overview_sources)
    for public_id, physical_sources in overview_sources.items():
        public_block = platform_block(overview, public_id)
        assert "platform: template" in public_block, public_id
        assert "update_interval: never" in public_block, public_id
        assert "force_update: true" in public_block, public_id
        assert "lambda:" not in public_block, public_id
        publish = f"id({public_id}).publish_state(x);"
        for source, source_id, topology_gate in physical_sources:
            source_block = platform_block(source, source_id)
            assert "platform: modbus_controller" in source_block, source_id
            assert "force_update: true" in source_block, source_id
            assert publish in source_block, source_id
            for other_public_id in public_poll_entities:
                if other_public_id != public_id:
                    assert (
                        f"id({other_public_id}).publish_state(x);"
                        not in source_block
                    ), f"{source_id} must not publish {other_public_id}"
            assert "parallel_topology_last_readback_ms" in source_block, source_id
            assert topology_gate in source_block, source_id

    soc_source = platform_block(battery, "battery_soc_1909")
    assert "modbus_controller_id: ${modbus_fast_controller_id}" in soc_source

    # Unchanged BMS limits are still successful physical FC03 samples.  They
    # must reach HA as reports so the signed-age fail-closed contract does not
    # confuse a stable limit with a dead Modbus source.
    for component_id in (
        "battery_voltage_1911",
        "max_charge_current_1916",
        "max_discharge_current_1917",
    ):
        block = platform_block(battery, component_id)
        assert "platform: modbus_controller" in block, component_id
        assert "force_update: true" in block, component_id

    for public_id, source_id in (
        ("inv_active_power_30007", "inv_active_power_master_30007"),
        ("battery_power_30009", "battery_power_master_30009"),
    ):
        public_block = platform_block(overview, public_id)
        assert "update_interval: never" in public_block, public_id
        assert "lambda:" not in public_block, public_id
        assert f"id({public_id}).publish_state(x);" in platform_block(
            overview, source_id
        )
        other_public_id = (
            "battery_power_30009"
            if public_id == "inv_active_power_30007"
            else "inv_active_power_30007"
        )
        assert f"id({other_public_id}).publish_state(x);" not in platform_block(
            overview, source_id
        )
    assert "id(inv_active_power_30007).publish_state(inverter_power);" in load
    assert "id(battery_power_30009).publish_state(" in load
    for timestamp in (
        "overview_master_pv_last_readback_ms",
        "overview_master_grid_last_readback_ms",
    ):
        assert overview.count(f"id: {timestamp}") == 1, timestamp
        all_firmware = overview + pv + meters + load
        assert all_firmware.count(f"id({timestamp})") >= 1, timestamp
    assert "overview_master_load_last_readback_ms" not in overview + load

    catalog_names = {record["source_name"] for record in catalog}
    for source_name in (
        "EMS Control Readback Generation",
        "GCF Control Readback Generation",
        "Battery Charge Power Readback Generation",
        "EMS Mode Readback Code",
        "EMS Self-Use SOC Readback",
        "EMS Backup SOC Readback",
        "EMS Force Charge SOC Readback",
        "EMS Maximum Charge Power Readback",
        "EMS Force Discharge SOC Readback",
        "EMS Maximum Discharge Power Readback",
        "GCF Enable Readback Code",
        "GCF Maximum Export Power Readback",
        "Battery Max Charge Power Readback",
        "Parallel Topology Readback Generation",
        "EMS Verified Hardware Readback Supported",
        "Direct Register Verified Readback Supported",
    ):
        assert source_name in catalog_names, source_name
    capability_records = [
        record
        for record in catalog
        if record.get("source_name") == "EMS Verified Hardware Readback Supported"
    ]
    assert len(capability_records) == 1
    assert capability_records[0]["domain"] == "sensor"
    assert capability_records[0]["source_component"] == "sensor"
    direct_capability_records = [
        record
        for record in catalog
        if record.get("source_name")
        == "Direct Register Verified Readback Supported"
    ]
    assert len(direct_capability_records) == 1
    assert direct_capability_records[0]["domain"] == "sensor"
    assert direct_capability_records[0]["source_component"] == "sensor"

    print("Firmware FC03 readback contract: PASS")


if __name__ == "__main__":
    main()

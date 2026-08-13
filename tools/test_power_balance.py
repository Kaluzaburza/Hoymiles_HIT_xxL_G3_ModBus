"""Standalone regression tests for parallel-system power balancing."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hoymiles_hit_modbus"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_power_balance_module():
    path = COMPONENT / "power_balance.py"
    spec = importlib.util.spec_from_file_location("hoymiles_power_balance", path)
    require(spec is not None and spec.loader is not None, "Cannot load power balance")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_sensor_behavior_class(sensor_source: str, power_balance):
    """Execute the production availability/value methods with lightweight HA stubs."""

    class FakeEntity:
        pass

    class FakeProxyEntity(FakeEntity):
        @property
        def source_state(self):
            return self._source_state

        @property
        def available(self) -> bool:
            source = self.source_state
            return source is not None and source.state not in {
                "unknown",
                "unavailable",
            }

    class FakeSensorEntity(FakeEntity):
        pass

    tree = ast.parse(sensor_source)
    sensor_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HoymilesSensor"
    )
    behavior_methods = {"available", "_mirrored_native_value", "native_value"}
    sensor_class.body = [
        node
        for node in sensor_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in behavior_methods
    ]
    require(
        {
            node.name
            for node in sensor_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        == behavior_methods,
        "Cannot isolate HoymilesSensor behavior methods",
    )
    ast.fix_missing_locations(sensor_class)
    namespace = {
        "Any": object,
        "HoymilesProxyEntity": FakeProxyEntity,
        "SensorEntity": FakeSensorEntity,
        "PARALLEL_POWER_TARGETS": power_balance.PARALLEL_POWER_TARGETS,
        "localized_text_state": lambda value, _language: value,
        "select_overview_power": power_balance.select_overview_power,
    }
    exec(
        compile(
            ast.Module(body=[sensor_class], type_ignores=[]),
            str(COMPONENT / "sensor.py"),
            "exec",
        ),
        namespace,
    )
    sensor_type = namespace["HoymilesSensor"]
    require(
        sensor_type.__mro__[1] is FakeProxyEntity,
        "Behavior harness does not preserve proxy-first MRO",
    )
    return sensor_type


def assert_sensor_proxy_behavior(sensor_source: str, power_balance) -> None:
    """Exercise ordinary proxy mirroring and parallel-target fail-closed behavior."""
    sensor_type = load_sensor_behavior_class(sensor_source, power_balance)

    ordinary = sensor_type.__new__(sensor_type)
    ordinary._catalog = {
        "translation_key": "ems_mode_readback_code",
        "source_component": "sensor",
    }
    ordinary._source_state = SimpleNamespace(state="0.0")
    require(
        ordinary.available is True and ordinary.native_value == 0.0,
        "An ordinary EMS readback without topology is not mirrored",
    )
    ordinary._source_state = SimpleNamespace(state="unavailable")
    require(
        ordinary.available is False and ordinary.native_value is None,
        "An unavailable ordinary source is exposed by its proxy",
    )

    target = sensor_type.__new__(sensor_type)
    target._catalog = {
        "translation_key": power_balance.OVERVIEW_BATTERY_POWER,
        "source_component": "sensor",
    }
    target._source_state = SimpleNamespace(state="2500")
    target._parallel_topology_known = False
    target._parallel_source_state = lambda _key: SimpleNamespace(state="unknown")
    target._parallel_power_value = lambda: None
    require(
        target.available is False and target.native_value is None,
        "Unknown topology does not fail closed for a parallel power target",
    )

    target._parallel_topology_known = True
    target._parallel_master_declared = True
    target._is_parallel_master = True
    target._parallel_source_state = lambda _key: SimpleNamespace(state="1")
    require(
        target.available is False and target.native_value is None,
        "An incomplete Master balance exposes the wrapped native target",
    )

    target._parallel_power_value = lambda: 33_856.0
    require(
        target.available is True and target.native_value == 33_856.0,
        "A complete Master balance is not exposed by the target proxy",
    )


def main() -> None:
    power_balance = load_power_balance_module()

    discharge = power_balance.calculate_parallel_power_balance(
        pv_power=0.0,
        grid_power=32_000.0,
        load_power=1_856.0,
    )
    require(discharge is not None, "A finite discharge balance was rejected")
    require(
        discharge.inverter_active_power == 33_856.0,
        "Parallel inverter output does not include exported and consumed power",
    )
    require(
        discharge.battery_power == 33_856.0,
        "High parallel discharge was not kept positive past the S_WORD limit",
    )
    require(
        power_balance.select_overview_power(
            power_balance.OVERVIEW_BATTERY_POWER,
            machine_type=1,
            source_power=-31_680.0,
            derived_power=discharge.battery_power,
        )
        == 33_856.0,
        "A complete Master balance did not replace the wrapped battery source",
    )
    require(
        power_balance.select_overview_power(
            power_balance.OVERVIEW_INVERTER_ACTIVE_POWER,
            machine_type=1,
            source_power=-31_680.0,
            derived_power=discharge.inverter_active_power,
        )
        == 33_856.0,
        "A complete Master balance did not replace the wrapped inverter source",
    )
    require(
        power_balance.select_overview_power(
            power_balance.OVERVIEW_BATTERY_POWER,
            machine_type=0,
            source_power=2_500.0,
            derived_power=discharge.battery_power,
        )
        == 2_500.0,
        "A single-inverter battery value must remain unchanged",
    )
    require(
        power_balance.select_overview_power(
            power_balance.OVERVIEW_BATTERY_POWER,
            machine_type=2,
            source_power=1_700.0,
            derived_power=discharge.battery_power,
        )
        == 1_700.0,
        "A Slave battery value must remain unchanged",
    )
    require(
        power_balance.select_overview_power(
            power_balance.OVERVIEW_BATTERY_POWER,
            machine_type="unknown",
            source_power=-4_000.0,
            derived_power=discharge.battery_power,
        )
        is None,
        "Unknown topology must fail closed instead of exposing a wrapped source",
    )
    require(
        power_balance.select_overview_power(
            power_balance.OVERVIEW_BATTERY_POWER,
            machine_type=1,
            source_power=-5_000.0,
            derived_power=None,
        )
        is None,
        "An incomplete Master balance must not expose a wrapped native source",
    )

    charge = power_balance.calculate_parallel_power_balance(
        pv_power=0.0,
        grid_power=-12_000.0,
        load_power=2_000.0,
    )
    require(charge is not None, "A finite charge balance was rejected")
    require(
        charge.inverter_active_power == -10_000.0,
        "Grid charging did not produce negative inverter power",
    )
    require(
        charge.battery_power == -10_000.0,
        "Grid charging did not preserve the negative battery sign",
    )
    require(
        power_balance.calculate_parallel_inverter_power(
            grid_power=32_000.0,
            load_power=1_856.0,
        )
        == 33_856.0,
        "Inverter power must be computable without a PV reading",
    )

    pv_charge = power_balance.calculate_parallel_power_balance(
        pv_power=10_000.0,
        grid_power=2_000.0,
        load_power=3_000.0,
    )
    require(pv_charge is not None, "A PV charging balance was rejected")
    require(
        pv_charge.inverter_active_power == 5_000.0
        and pv_charge.battery_power == -5_000.0,
        "PV charging while exporting has the wrong battery direction",
    )

    require(
        power_balance.calculate_parallel_power_balance(
            pv_power=float("nan"),
            grid_power=0.0,
            load_power=0.0,
        )
        is None,
        "A non-finite balance source must fail closed",
    )
    for machine_type, expected in (
        (0, False),
        (1, True),
        (1.0, True),
        (2, False),
        ("unknown", False),
        (None, False),
        (float("nan"), False),
        (float("inf"), False),
    ):
        require(
            power_balance.is_parallel_master(machine_type) is expected,
            f"Unexpected topology decision for {machine_type!r}",
        )
    for machine_type, expected in (
        (0, True),
        (1, True),
        (2, True),
        ("unknown", False),
        (None, False),
        (float("nan"), False),
    ):
        require(
            power_balance.is_known_machine_type(machine_type) is expected,
            f"Unexpected known-topology decision for {machine_type!r}",
        )

    catalog = json.loads(
        (COMPONENT / "entity_catalog.json").read_text(encoding="utf-8")
    )
    sensor_keys = {
        record["translation_key"]
        for record in catalog
        if record["domain"] == "sensor"
    }
    require(
        set(power_balance.PARALLEL_POWER_SOURCE_KEYS) <= sensor_keys,
        "A parallel balance source is missing from the sensor catalog",
    )
    require(
        power_balance.PARALLEL_POWER_TARGETS <= sensor_keys,
        "A parallel balance target is missing from the sensor catalog",
    )
    require(
        power_balance.PARALLEL_POWER_SOURCE_KEYS[0] == "machines_type",
        "Topology changes must remain part of the balance subscription",
    )
    require(
        "overview_pv_total_power"
        not in power_balance.PARALLEL_POWER_SOURCE_KEYS_BY_TARGET[
            power_balance.OVERVIEW_INVERTER_ACTIVE_POWER
        ],
        "The inverter proxy must not depend on or subscribe to PV",
    )
    sensor_source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    assert_sensor_proxy_behavior(sensor_source, power_balance)
    require(
        "from .energy_data import numeric_state_sample" in sensor_source
        and "max_age_seconds=120.0" in sensor_source
        and "max_age_seconds=300.0" in sensor_source
        and "parallel_balance_unavailable" in sensor_source,
        "Parallel power proxies lack the shared freshness/fail-closed contract",
    )
    require(
        "if not self._parallel_topology_known:" in sensor_source
        and "return False" in sensor_source,
        "Unknown/stale topology can still expose a native parallel power value",
    )
    require(
        set(power_balance.PARALLEL_POWER_SOURCE_KEYS_BY_TARGET[
            power_balance.OVERVIEW_BATTERY_POWER
        ])
        == {
            "machines_type",
            "overview_pv_total_power",
            "overview_grid_total_active_power",
            "overview_load_active_power",
        },
        "The battery proxy does not subscribe to its exact balance sources",
    )
    require(
        set(power_balance.PARALLEL_POWER_SOURCE_KEYS_BY_TARGET[
            power_balance.OVERVIEW_INVERTER_ACTIVE_POWER
        ])
        == {
            "machines_type",
            "overview_grid_total_active_power",
            "overview_load_active_power",
        },
        "The inverter proxy does not subscribe to its exact balance sources",
    )

    print("Parallel power-balance regression tests passed")


if __name__ == "__main__":
    main()

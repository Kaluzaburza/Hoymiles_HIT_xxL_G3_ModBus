"""Power-balance helpers for parallel Hoymiles systems."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


OVERVIEW_BATTERY_POWER = "overview_battery_power"
OVERVIEW_INVERTER_ACTIVE_POWER = "overview_inverter_active_power"

PARALLEL_POWER_TARGETS = frozenset(
    {
        OVERVIEW_BATTERY_POWER,
        OVERVIEW_INVERTER_ACTIVE_POWER,
    }
)
PARALLEL_POWER_SOURCE_KEYS_BY_TARGET = {
    OVERVIEW_BATTERY_POWER: (
        "machines_type",
        "overview_pv_total_power",
        "overview_grid_total_active_power",
        "overview_load_active_power",
    ),
    OVERVIEW_INVERTER_ACTIVE_POWER: (
        "machines_type",
        "overview_grid_total_active_power",
        "overview_load_active_power",
    ),
}
PARALLEL_POWER_SOURCE_KEYS = tuple(
    dict.fromkeys(
        source_key
        for source_keys in PARALLEL_POWER_SOURCE_KEYS_BY_TARGET.values()
        for source_key in source_keys
    )
)


@dataclass(frozen=True, slots=True)
class ParallelPowerBalance:
    """Derived system-wide power values in watts."""

    inverter_active_power: float
    battery_power: float


def is_parallel_master(machine_type: object) -> bool:
    """Return whether the topology value explicitly identifies a Master."""
    try:
        numeric_type = float(machine_type)
    except (TypeError, ValueError):
        return False
    return isfinite(numeric_type) and numeric_type == 1.0


def is_known_machine_type(machine_type: object) -> bool:
    """Return whether topology explicitly identifies Master/single/Slave.

    Unknown topology is not a compatibility signal.  Falling back to a native
    16-bit value while a parallel Master topology sample is unavailable can
    invert battery direction, so callers must fail closed until the topology
    is positively known again.
    """
    try:
        numeric_type = float(machine_type)
    except (TypeError, ValueError):
        return False
    return isfinite(numeric_type) and numeric_type in {0.0, 1.0, 2.0}


def calculate_parallel_power_balance(
    *,
    pv_power: float,
    grid_power: float,
    load_power: float,
) -> ParallelPowerBalance | None:
    """Calculate inverter and battery power using the documented sign convention.

    Grid power is positive while exporting and negative while importing. Battery
    power is positive while discharging and negative while charging. The
    manufacturer's overview PV and LOAD values are treated as system totals.
    """
    values = (pv_power, grid_power, load_power)
    if not all(isfinite(value) for value in values):
        return None

    inverter_active_power = calculate_parallel_inverter_power(
        grid_power=grid_power,
        load_power=load_power,
    )
    if inverter_active_power is None:
        return None
    battery_power = inverter_active_power - pv_power
    return ParallelPowerBalance(
        inverter_active_power=inverter_active_power,
        battery_power=battery_power,
    )


def calculate_parallel_inverter_power(
    *,
    grid_power: float,
    load_power: float,
) -> float | None:
    """Calculate inverter power without requiring an unrelated PV reading."""
    if not isfinite(grid_power) or not isfinite(load_power):
        return None
    return load_power + grid_power


def select_overview_power(
    translation_key: str,
    *,
    machine_type: object,
    source_power: float | None,
    derived_power: float | None,
) -> float | None:
    """Select a complete Master balance or preserve a non-Master source.

    The native 16-bit Master value is known to wrap on larger parallel plants.
    Falling back to it when one balance input is missing can therefore invert
    battery direction.  An explicitly identified Master must fail closed until
    the complete derived balance is available.
    """
    if is_parallel_master(machine_type):
        return (
            derived_power
            if translation_key in PARALLEL_POWER_TARGETS
            else source_power
        )
    return source_power if is_known_machine_type(machine_type) else None

"""Cross-system simulation matrix for all automatic EMS planners.

The suite deliberately uses only the pure optimizer modules.  It therefore
cannot switch a real inverter and can be run before every public release.
Besides named household scenarios it performs deterministic parameter sweeps
for HIT 10/15/20 kW and a two-inverter 40 kW system.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil, sin, pi
from pathlib import Path
from random import Random
import sys
from zoneinfo import ZoneInfo

try:
    import yaml
except ImportError:  # Source-order checks below still run without PyYAML.
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "hoymiles_hit_modbus"))

from rce_optimizer import OptimizerInput, PriceSlot, optimize_rce  # noqa: E402
from rcm_optimizer import RCMOptimizerInput, optimize_rcm  # noqa: E402
from tariff_optimizer import (  # noqa: E402
    TariffOptimizerInput,
    TariffSchedule,
    optimize_tariff_charging,
)


WARSAW = ZoneInfo("Europe/Warsaw")
EPSILON = 1e-5


@dataclass(frozen=True, slots=True)
class SystemModel:
    """Representative installation used by the release simulations."""

    name: str
    inverter_kw: float
    inverter_count: int
    battery_kwh: float
    pv_daily_kwh: float
    home_daily_kwh: float
    night_load_kwh: float
    battery_voltage_v: float
    bms_charge_a: float
    bms_discharge_a: float

    @property
    def system_kw(self) -> float:
        return self.inverter_kw * self.inverter_count

    @property
    def bms_charge_kw(self) -> float:
        return self.battery_voltage_v * self.bms_charge_a / 1000.0

    @property
    def bms_discharge_kw(self) -> float:
        return self.battery_voltage_v * self.bms_discharge_a / 1000.0


SYSTEMS = (
    SystemModel("HIT-10 + 10 kWh", 10.0, 1, 10.2, 12.0, 8.0, 3.2, 52.0, 100.0, 100.0),
    SystemModel("HIT-15 + 21 kWh", 15.0, 1, 21.0, 28.0, 16.0, 6.5, 52.5, 175.0, 175.0),
    SystemModel("HIT-20 + 40 kWh", 20.0, 1, 40.0, 55.0, 28.0, 11.0, 53.0, 250.0, 250.0),
    SystemModel("2x HIT-20 + 230 kWh", 20.0, 2, 230.0, 120.0, 48.0, 19.0, 53.0, 700.0, 700.0),
)


def _half_hours(start: datetime, end: datetime):
    cursor = start
    while cursor < end:
        yield cursor
        cursor += timedelta(minutes=30)


def pv_profile(
    first_day: datetime,
    today_kwh: float,
    tomorrow_kwh: float,
) -> dict[datetime, float]:
    """Create a smooth 06:00-18:00 two-day PV forecast."""

    result: dict[datetime, float] = {}
    for day_offset, total in ((0, today_kwh), (1, tomorrow_kwh)):
        starts = [
            first_day.replace(hour=6, minute=0) + timedelta(
                days=day_offset,
                minutes=30 * index,
            )
            for index in range(24)
        ]
        weights = [sin(pi * (index + 0.5) / len(starts)) for index in range(len(starts))]
        weight_sum = sum(weights)
        for start, weight in zip(starts, weights):
            result[start] = max(total, 0.0) * weight / weight_sum
    return result


def rce_prices(first_day: datetime, pattern: str) -> list[PriceSlot]:
    """Create two complete days of market prices and a 22:00-06:00 lockout."""

    slots: list[PriceSlot] = []
    end = first_day.replace(hour=0, minute=0) + timedelta(days=2)
    for start in _half_hours(first_day.replace(hour=0, minute=0), end):
        hour = start.hour + start.minute / 60.0
        if pattern == "tomorrow_peak":
            price = 1.15 if start.date() > first_day.date() and 6 <= hour < 9 else 0.32
            if start.date() == first_day.date() and 18 <= hour < 21:
                price = 0.78
        elif pattern == "today_peak":
            price = 1.25 if start.date() == first_day.date() and 18 <= hour < 21 else 0.55
        elif pattern == "volatile":
            if 10 <= hour < 15:
                price = -0.18
            elif 18 <= hour < 21:
                price = 1.40 if start.date() == first_day.date() else 1.05
            elif 6 <= hour < 9:
                price = 0.82
            else:
                price = 0.20
        elif pattern == "all_negative":
            price = -0.25
        else:
            price = 0.12
        blocked = hour >= 22 or hour < 6
        slots.append(PriceSlot(start=start, price_pln_kwh=price, blocked=blocked))
    return slots


def tariff_schedule(kind: str) -> TariffSchedule:
    """Return a stable synthetic schedule with realistic price ordering."""

    return TariffSchedule(
        tariff_type=kind,
        g11_price_pln_kwh=0.90,
        low_price_pln_kwh=0.55,
        medium_price_pln_kwh=0.82,
        peak_price_pln_kwh=1.18,
        cheap_windows=((22 * 60, 6 * 60), (13 * 60, 15 * 60)),
        medium_windows=((7 * 60, 13 * 60),),
        weekend_low_price=kind.casefold() in {"g12w", "g13"},
        polish_holidays_low_price=True,
    )


def assert_rce_invariants(settings: OptimizerInput, result) -> None:
    """Check safety, power, reserve and profitability invariants."""

    system_kw = settings.inverter_power_kw * settings.inverter_count
    requested_kw = system_kw * min(max(settings.discharge_power_percent, 0.0), 100.0) / 100.0
    assert 0.0 <= result.minimum_soc_percent <= 100.0
    assert 0.0 <= result.base_reserve_energy_kwh <= settings.battery_capacity_kwh + EPSILON
    assert 0.0 <= result.protected_home_energy_kwh <= settings.battery_capacity_kwh + EPSILON
    assert result.available_energy_now_kwh >= -EPSILON
    assert result.maximum_export_power_kw <= requested_kw + EPSILON
    assert abs(result.total_export_kwh - (result.planned_export_kwh + result.natural_export_kwh)) < EPSILON
    assert abs(result.total_revenue_pln - (result.planned_revenue_pln + result.natural_revenue_pln)) < EPSILON
    assert result.status_code != "optimizer_error"
    price_lookup = {slot.start: slot for slot in settings.price_slots}
    for item in result.planned_exports:
        assert item.start in price_lookup
        assert not price_lookup[item.start].blocked
        assert item.energy_kwh <= result.maximum_export_power_kw * 0.5 + 0.02
        assert item.energy_kwh >= 0.0
    if result.ready:
        assert result.ending_battery_kwh >= result.base_reserve_energy_kwh - EPSILON
        assert result.total_revenue_pln >= result.uncontrolled_revenue_pln - EPSILON
    else:
        assert not result.planned_exports
    if result.bms_discharge_power_limit_kw is not None:
        assert result.maximum_export_power_kw <= result.bms_discharge_power_limit_kw + EPSILON
    # With a full battery and unavoidable PV overflow the optimizer may choose
    # the least-bad negative-price slot.  With no natural overflow it must not
    # deliberately discharge the battery at a non-positive price.
    if (
        all(slot.price_pln_kwh <= 0 for slot in settings.price_slots)
        and result.uncontrolled_export_kwh <= EPSILON
    ):
        assert not result.planned_exports


def run_rce_matrix(*, exhaustive: bool = True) -> tuple[int, Counter[str]]:
    """Exercise price selection, night protection, BMS limits and parallel power."""

    now = datetime(2026, 8, 10, 6, 0, tzinfo=WARSAW)
    statuses: Counter[str] = Counter()
    count = 0
    pv_factors = (0.0, 0.25, 1.0, 1.6) if exhaustive else (0.0, 1.6)
    tomorrow_factors = (0.1, 0.7, 1.3) if exhaustive else (0.1, 1.3)
    soc_values = (18.0, 55.0, 98.0) if exhaustive else (18.0, 98.0)
    for model in SYSTEMS:
        for pv_factor in pv_factors:
            for tomorrow_factor in tomorrow_factors:
                for soc in soc_values:
                    for price_pattern in ("tomorrow_peak", "today_peak", "volatile", "all_negative"):
                        settings = OptimizerInput(
                            now=now,
                            price_slots=rce_prices(now, price_pattern),
                            pv_by_slot_kwh=pv_profile(
                                now,
                                model.pv_daily_kwh * pv_factor,
                                model.pv_daily_kwh * tomorrow_factor,
                            ),
                            battery_capacity_kwh=model.battery_kwh,
                            battery_soc_percent=soc,
                            outage_reserve_soc_percent=20.0,
                            safety_margin_soc_percent=2.0,
                            manual_minimum_soc_percent=22.0,
                            dynamic_reserve_enabled=True,
                            average_daily_load_kwh=model.home_daily_kwh,
                            average_night_load_kwh=model.night_load_kwh,
                            night_start_minute=20 * 60,
                            night_end_minute=7 * 60,
                            inverter_power_kw=model.inverter_kw,
                            inverter_count=model.inverter_count,
                            discharge_power_percent=80.0,
                            export_efficiency_percent=94.0,
                            bms_max_discharge_current_a=model.bms_discharge_a,
                            battery_voltage_v=model.battery_voltage_v,
                            bms_power_safety_percent=95.0,
                        )
                        result = optimize_rce(settings)
                        assert_rce_invariants(settings, result)
                        statuses[result.status_code] += 1
                        count += 1
    return count, statuses


def assert_tariff_invariants(settings: TariffOptimizerInput, result) -> None:
    """Check battery bounds, shared Grid Charge power and economic monotonicity."""

    assert -EPSILON <= result.ending_battery_kwh <= settings.battery_capacity_kwh + EPSILON
    assert 0.0 <= result.ending_battery_soc_percent <= 100.0 + EPSILON
    assert 0.0 <= result.target_soc_percent <= 100.0 + EPSILON
    assert result.remaining_shortage_kwh <= result.baseline_shortage_kwh + EPSILON
    assert result.planned_grid_import_kwh >= -EPSILON
    assert result.planned_stored_energy_kwh >= -EPSILON
    assert result.planned_direct_load_kwh >= -EPSILON
    reserve_gap = max(settings.reserve_soc_percent - settings.battery_soc_percent, 0.0) / 100.0 * settings.battery_capacity_kwh
    reserve_cost = reserve_gap / max(settings.charge_efficiency_percent / 100.0, 0.01) * settings.schedule.low_price_pln_kwh
    assert result.optimized_grid_cost_pln <= result.baseline_grid_cost_pln + reserve_cost + 0.02
    for item in result.planned_charges:
        assert item.grid_import_kwh <= result.charge_power_kw * 0.5 + EPSILON
        assert item.direct_load_kwh <= item.grid_import_kwh + EPSILON
        assert item.stored_energy_kwh <= max(item.grid_import_kwh - item.direct_load_kwh, 0.0) * settings.charge_efficiency_percent / 100.0 + EPSILON
        if settings.battery_charge_power_kw is not None:
            assert item.stored_energy_kwh <= settings.battery_charge_power_kw * 0.5 + EPSILON
        assert 0.0 <= item.target_soc_percent <= 100.0 + EPSILON
    if settings.schedule.tariff_type.casefold().replace(" ", "") == "g11":
        assert not result.planned_charges
        assert result.planned_grid_import_kwh == 0.0
    if result.next_charge_start is not None:
        assert any(item.start == result.next_charge_start for item in result.planned_charges)


def run_tariff_matrix(*, exhaustive: bool = True) -> tuple[int, Counter[str]]:
    """Exercise G11/G12/G12w/G13, weak PV, winter load and limited BMS power."""

    statuses: Counter[str] = Counter()
    count = 0
    all_moments = (
        datetime(2026, 8, 10, 5, 40, tzinfo=WARSAW),
        datetime(2026, 8, 10, 12, 20, tzinfo=WARSAW),
        datetime(2026, 8, 10, 14, 50, tzinfo=WARSAW),
        datetime(2026, 8, 10, 21, 10, tzinfo=WARSAW),
        datetime(2026, 8, 8, 9, 10, tzinfo=WARSAW),
    )
    moments = all_moments if exhaustive else (all_moments[0], all_moments[2])
    pv_factors = (0.0, 0.2, 1.2) if exhaustive else (0.0, 1.2)
    soc_values = (15.0, 45.0, 90.0) if exhaustive else (15.0, 90.0)
    for model in SYSTEMS:
        for kind in ("G11", "G12", "G12w", "G13"):
            for now in moments:
                for pv_factor in pv_factors:
                    for soc in soc_values:
                        charge_power = model.system_kw * 0.8
                        settings = TariffOptimizerInput(
                            now=now,
                            pv_by_slot_kwh=pv_profile(
                                now,
                                model.pv_daily_kwh * pv_factor,
                                model.pv_daily_kwh * (0.15 if pv_factor < 0.3 else 0.8),
                            ),
                            battery_capacity_kwh=model.battery_kwh,
                            battery_soc_percent=soc,
                            reserve_soc_percent=22.0,
                            maximum_soc_percent=95.0,
                            average_daily_load_kwh=model.home_daily_kwh * (1.6 if pv_factor == 0 else 1.0),
                            average_night_load_kwh=model.night_load_kwh,
                            night_start_minute=20 * 60,
                            night_end_minute=7 * 60,
                            charge_power_kw=charge_power,
                            charge_efficiency_percent=93.0,
                            discharge_efficiency_percent=94.0,
                            minimum_saving_pln_kwh=0.04,
                            schedule=tariff_schedule(kind),
                            battery_charge_power_kw=min(model.bms_charge_kw, model.system_kw),
                            battery_discharge_power_kw=min(model.bms_discharge_kw, model.system_kw),
                            pv_charge_power_kw=min(model.bms_charge_kw, model.system_kw),
                        )
                        result = optimize_tariff_charging(settings)
                        assert_tariff_invariants(settings, result)
                        statuses[result.status_code] += 1
                        count += 1
    return count, statuses


def assert_rcm_invariants(settings: RCMOptimizerInput, result) -> None:
    """Check SOC floors, BMS power, export cap and voltage emergency behavior."""

    assert result.maximum_voltage_v == max(settings.voltage_l1_v, settings.voltage_l2_v, settings.voltage_l3_v)
    assert 0.0 <= result.voltage_risk_score <= 100.0
    assert 0.0 <= result.required_headroom_kwh <= settings.battery_capacity_kwh + EPSILON
    assert result.available_headroom_kwh >= -EPSILON
    assert result.planned_grid_discharge_kwh >= -EPSILON
    energy_above_floor = max(settings.battery_capacity_kwh * (settings.battery_soc_percent - result.protected_minimum_soc_percent) / 100.0, 0.0)
    assert result.planned_grid_discharge_kwh <= energy_above_floor + EPSILON
    assert result.pre_discharge_target_soc_percent >= result.protected_minimum_soc_percent - EPSILON
    assert 10.0 <= result.recommended_charge_limit_percent <= 100.0
    assert result.recommended_charge_power_kw <= result.bms_charge_power_limit_kw + EPSILON
    assert 0.0 <= result.recommended_export_limit_percent <= result.effective_export_cap_percent + EPSILON
    assert result.pre_discharge_power_kw <= settings.system_power_kw + max(settings.load_power_kw, 0.0) + EPSILON
    if settings.battery_voltage_v and settings.bms_max_discharge_current_a:
        bms_discharge_kw = (
            settings.battery_voltage_v
            * settings.bms_max_discharge_current_a
            / 1000.0
        )
        # Result values are rounded for Home Assistant presentation.
        assert result.pre_discharge_power_kw <= bms_discharge_kw + 0.011, (
            result.pre_discharge_power_kw,
            bms_discharge_kw,
        )
    if result.maximum_voltage_v >= 253.0 and settings.export_control_enabled:
        assert result.status_code == "emergency"
        assert result.recommended_export_limit_percent == 0.0
    if result.pre_discharge_ready:
        assert result.action == "grid_discharge_preparation"
        assert (settings.minutes_to_risk or 0) > 30
        assert settings.risk_day_offset == 0


def run_rcm_matrix(*, exhaustive: bool = True) -> tuple[int, Counter[str]]:
    """Exercise stable, warning, emergency, full-battery and no-export states."""

    statuses: Counter[str] = Counter()
    count = 0
    now = datetime(2026, 8, 10, 11, 0, tzinfo=WARSAW)
    voltages = (
        (240.0, 248.6, 250.2, 251.2, 252.4, 253.2)
        if exhaustive
        else (240.0, 250.2, 252.4, 253.2)
    )
    soc_values = (18.0, 60.0, 98.0) if exhaustive else (18.0, 98.0)
    pv_factors = (0.0, 0.6, 1.2) if exhaustive else (0.0, 1.2)
    for model in SYSTEMS:
        for voltage in voltages:
            for soc in soc_values:
                for pv_factor in pv_factors:
                    for export_cap in (0.0, 50.0, 100.0):
                        pv_kw = model.system_kw * pv_factor
                        load_kw = min(model.home_daily_kwh / 12.0, model.system_kw * 0.6)
                        settings = RCMOptimizerInput(
                            now=now,
                            voltage_l1_v=voltage,
                            voltage_l2_v=voltage - 0.6,
                            voltage_l3_v=voltage - 1.1,
                            filtered_voltage_v=voltage - 0.2,
                            rolling_10m_voltage_v=voltage - 0.4,
                            historical_p90_voltage_v=max(voltage - 0.8, 240.0),
                            risk_windows=((12 * 60, 15 * 60, 254.0),),
                            history_days=4,
                            pv_power_kw=pv_kw,
                            load_power_kw=load_kw,
                            grid_export_power_kw=max(pv_kw - load_kw, 0.0),
                            battery_capacity_kwh=model.battery_kwh,
                            battery_soc_percent=soc,
                            reserve_soc_percent=20.0,
                            safety_margin_soc_percent=2.0,
                            protected_minimum_soc_percent=30.0,
                            expected_risk_surplus_kwh=model.pv_daily_kwh * 0.25,
                            expected_natural_headroom_kwh=model.home_daily_kwh * 0.05,
                            minutes_to_risk=60,
                            risk_day_offset=0,
                            system_power_kw=model.system_kw,
                            battery_voltage_v=model.battery_voltage_v,
                            bms_max_charge_current_a=model.bms_charge_a,
                            bms_max_discharge_current_a=model.bms_discharge_a,
                            current_charge_limit_percent=50.0,
                            saved_charge_limit_percent=90.0,
                            export_control_enabled=True,
                            current_export_limit_percent=export_cap,
                            saved_export_limit_percent=export_cap,
                            user_export_cap_percent=export_cap,
                            charge_efficiency_percent=94.0,
                        )
                        result = optimize_rcm(settings)
                        assert_rcm_invariants(settings, result)
                        statuses[result.status_code] += 1
                        count += 1
    return count, statuses


def run_randomized_boundary_sweep(*, samples: int = 120) -> int:
    """Add reproducible edge values not covered by the representative systems."""

    random = Random(20260808)
    now = datetime(2026, 8, 10, 6, 0, tzinfo=WARSAW)
    count = 0
    for _ in range(samples):
        inverter_kw = random.choice((5.0, 8.0, 10.0, 12.0, 15.0, 20.0))
        inverter_count = random.randint(1, 3)
        capacity = random.uniform(5.0, 230.0)
        voltage = random.uniform(48.0, 58.0)
        discharge_a = random.uniform(50.0, 700.0)
        settings = OptimizerInput(
            now=now,
            price_slots=rce_prices(now, random.choice(("tomorrow_peak", "volatile", "flat"))),
            pv_by_slot_kwh=pv_profile(now, random.uniform(0.0, 150.0), random.uniform(0.0, 150.0)),
            battery_capacity_kwh=capacity,
            battery_soc_percent=random.uniform(0.0, 100.0),
            outage_reserve_soc_percent=random.uniform(0.0, 45.0),
            safety_margin_soc_percent=random.uniform(0.0, 10.0),
            manual_minimum_soc_percent=random.uniform(0.0, 50.0),
            dynamic_reserve_enabled=random.choice((True, False)),
            average_daily_load_kwh=random.uniform(0.0, 90.0),
            average_night_load_kwh=random.uniform(0.0, 35.0),
            night_start_minute=20 * 60,
            night_end_minute=7 * 60,
            inverter_power_kw=inverter_kw,
            inverter_count=inverter_count,
            discharge_power_percent=random.uniform(0.0, 100.0),
            export_efficiency_percent=random.uniform(82.0, 99.0),
            bms_max_discharge_current_a=discharge_a,
            battery_voltage_v=voltage,
            bms_power_safety_percent=random.uniform(80.0, 98.0),
        )
        assert_rce_invariants(settings, optimize_rce(settings))
        count += 1
    return count


def assert_automation_interlocks() -> None:
    """Verify that the three controllers and service balancing cannot overlap."""

    source = (ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml").read_text(encoding="utf-8")
    required_markers = (
        "id: hoymiles_tariff_grid_charge_control",
        "input_boolean.hoymiles_rce_discharge_enabled",
        "input_boolean.hoymiles_battery_balancing_active",
        "id: hoymiles_rce_grid_discharge_control",
        "input_boolean.hoymiles_tariff_charge_enabled",
        "id: hoymiles_rcm_voltage_charge_control",
        "id: hoymiles_rcm_pre_discharge_control",
        "input_boolean.hoymiles_rcm_shadow_mode",
        "input_boolean.hoymiles_discharge_cycle_active",
        "input_boolean.hoymiles_charge_cycle_active",
        "is_state('timer.hoymiles_discharge', 'active')",
        "is_state('timer.hoymiles_charge', 'active')",
        "input_number.hoymiles_tariff_latched_target_soc",
        "input_datetime.hoymiles_tariff_latched_slot_end",
        "states.input_boolean.hoymiles_tariff_charge_active",
        "'current_slot_end'",
        'for: "00:00:45"',
        "input_datetime.hoymiles_rce_latched_slot_end",
        "binary_sensor.hoymiles_ems_execution_ready",
        "binary_sensor.hoymiles_rce_control_data_ready",
        "binary_sensor.hoymiles_tariff_control_data_ready",
        "binary_sensor.hoymiles_ems_export_allowed",
        "id: hoymiles_automatic_ems_control_failsafe",
        "states.sensor.hoymiles_hit_esp_uptime",
        "last_reported",
        "esp_age <= 180",
        "liveness_source: sensor.hoymiles_hit_esp_uptime",
        "price_age <= 300",
        "plan_age >= -5",
        "plan_age <= 300",
        "source_age >= -5",
        "source_age <= 1200",
        "input_text.hoymiles_ems_last_push_fingerprint",
        "as_timestamp(now()) - last >= 300",
        "end - as_timestamp(now()) >= 300",
        "Falownik nie potwierdził limitu ładowania",
        "Falownik nie potwierdził nowego limitu ładowania",
        "Falownik nie potwierdził celu SOC",
    )
    for marker in required_markers:
        assert marker in source, f"Missing automation interlock marker: {marker}"
    rce_ready = source.split(
        'name: "Hoymiles RCE Control Data Ready"', 1
    )[1].split('name: "Hoymiles Tariff', 1)[0]
    assert "or (source_age" not in rce_ready, (
        "A fresh raw price source must not authorize a stale/future RCE plan"
    )
    transient_stop = (
        "or not is_state(\n"
        "                       'binary_sensor.hoymiles_tariff_planned_charge_slot', 'on')"
    )
    assert transient_stop not in source, (
        "Tariff charging still stops immediately when the live plan flickers"
    )
    assert (
        "is_state('binary_sensor.hoymiles_ems_export_allowed', 'on')" in source
    ), "RCE control does not enforce the zero-export/GCF guard"
    assert (
        "is_state('sensor.hoymiles_ems_hardware_mode', 'grid_discharge') }}"
        in source
    ), "RCE command acknowledgement is not based on the actual EMS readback"
    balancing_start = source.split(
        "hoymiles_start_battery_balancing:", 1
    )[1].split("hoymiles_stop_battery_balancing:", 1)[0]
    assert balancing_start.index(
        "input_boolean.hoymiles_battery_balancing_active"
    ) < balancing_start.index('value: "handover"')
    balancing_stop = source.split(
        "hoymiles_stop_battery_balancing:", 1
    )[1].split("automation:", 1)[0]
    for marker in (
        "stopping_complete",
        "stopping_abort",
        "Ownership is released only",
        "is_state('sensor.hoymiles_ems_hardware_mode', 'self_use')",
        "sensor.hoymiles_hit_ems_maximum_charge_power_readback",
        "sensor.hoymiles_hit_ems_force_charge_soc_readback",
    ):
        assert marker in balancing_stop, f"Balancing restore lacks {marker}"
    release = balancing_stop.rsplit("input_boolean.turn_off", 1)[1]
    assert "input_boolean.hoymiles_battery_balancing_active" in release
    balancing_control = source.split(
        "- id: hoymiles_battery_balancing_control", 1
    )[1].split("id: hoymiles_rce", 1)[0]
    assert 'state: "handover"' in balancing_control
    assert "stopping_complete" in balancing_control
    assert "stopping_handover" in balancing_control
    assert "daylight PV phase has a hard Self-Use invariant" in balancing_control
    handover = balancing_control.split('state: "handover"', 1)[1].split(
        "daylight PV phase has a hard Self-Use invariant", 1
    )[0]
    assert "input_boolean.hoymiles_rcm_export_control_active" in handover, (
        "Balancing periodic handover can race an RCEm export restore"
    )
    assert handover.index(
        "input_number.hoymiles_battery_balancing_saved_charge_power"
    ) < handover.index('value: "pv"')
    assert handover.index(
        "input_number.hoymiles_battery_balancing_saved_force_charge_soc"
    ) < handover.index('value: "pv"')
    for snapshot_block in (balancing_start, handover):
        transition = snapshot_block.rsplit('value: "pv"', 1)[0]
        assert "is_number(states(\n" in transition
        assert "sensor.hoymiles_hit_ems_maximum_charge_power_readback" in transition
        assert "sensor.hoymiles_hit_ems_force_charge_soc_readback" in transition
        snapshot_tail = transition.rsplit(
            "input_number.hoymiles_battery_balancing_saved_charge_power",
            1,
        )[-1]
        assert "| float(50)" not in snapshot_tail
        assert "| float(100)" not in snapshot_tail
    for marker in (
        'name: "Hoymiles Battery Balancing Control Data Ready"',
        'name: "Hoymiles Battery Balancing BMS Safe Charge Power"',
        "binary_sensor.hoymiles_battery_balancing_control_data_ready",
        "soc_age >= -5 and soc_age <= 120",
        "current_age >= -5 and current_age <= 300",
        "voltage_age >= -5 and voltage_age <= 300",
        "sensor.hoymiles_battery_balancing_bms_safe_charge_power",
    ):
        assert marker in source, f"Balancing freshness/cap contract lacks {marker}"
    assert "and (soc.state | float(-1)) >= 0" in source
    assert "and (soc.state | float(101)) <= 100" in source
    balancing_control = source.split(
        "- id: hoymiles_battery_balancing_control", 1
    )[1].split("id: hoymiles_rce", 1)[0]
    hold_completion = balancing_control.split("id: hold_finished", 1)[1].split(
        "# Wy", 1
    )[0]
    restart_completion = balancing_control.split("# Po restarcie HA", 1)[1].split(
        "# Cykl", 1
    )[0]
    for completion in (hold_completion, restart_completion):
        assert "sensor.hoymiles_hit_overview_battery_soc" in completion
        assert "| float(-1)) >= 0" in completion
        assert "| float(101)) <= 100" in completion
    assert balancing_start.index(
        "binary_sensor.hoymiles_battery_balancing_control_data_ready"
    ) < balancing_start.index("action: input_boolean.turn_on")
    data_loss = balancing_control.index(
        "Any stale SOC, BMS limit, LOAD or writable-register readback"
    )
    automatic_start = balancing_control.index(
        "Cykl należny w danym dniu rozpoczyna się dopiero po wschodzie"
    )
    full_hold = balancing_control.index("Pełny magazyn:")
    assert data_loss < automatic_start < full_hold
    hold_block = balancing_control[full_hold:].split(
        "Po zachodzie PV nie odbuduje już magazynu", 1
    )[0]
    assert hold_block.index('option: "grid_charge"') < hold_block.index(
        '- delay: "00:00:05"'
    ) < hold_block.index("wait_template") < hold_block.index(
        "action: timer.start"
    )
    assert "continue_on_timeout: false" in hold_block


def assert_manual_cycle_finalization_contracts() -> None:
    """Protect manual owner recovery across timer expiry and HA restarts."""

    scheduler = (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml"
    ).read_text(encoding="utf-8")

    # A start owns the transaction before touching EMS. The watchdog grace
    # below is what makes that intentional owner=on/timer=idle phase safe.
    start_specs = (
        (
            "hoymiles_start_grid_discharge:",
            "hoymiles_start_grid_charge:",
            "input_boolean.hoymiles_discharge_cycle_active",
            'option: "grid_discharge"',
            "timer.hoymiles_discharge",
        ),
        (
            "hoymiles_start_grid_charge:",
            "hoymiles_stop_scheduled_cycle:",
            "input_boolean.hoymiles_charge_cycle_active",
            'option: "grid_charge"',
            "timer.hoymiles_charge",
        ),
    )
    for start_marker, end_marker, owner, mode_write, timer in start_specs:
        block = scheduler.split(start_marker, 1)[1].split(end_marker, 1)[0]
        owner_write = block.index("action: input_boolean.turn_on")
        assert owner in block[owner_write:]
        assert owner_write < block.index(mode_write) < block.index(
            "action: timer.start"
        )
        assert timer in block[block.index("action: timer.start") :]
        release_wait = block.index("wait_template", block.index(owner))
        mode_position = block.index(mode_write)
        readback_position = block.index(
            'state: "grid_discharge"'
            if "grid_discharge" in mode_write
            else 'state: "grid_charge"',
            mode_position,
        )
        timer_position = block.index("action: timer.start")
        for automatic_owner in (
            "input_boolean.hoymiles_rcm_active",
            "input_boolean.hoymiles_rcm_pre_discharge_active",
            "input_boolean.hoymiles_rcm_export_control_active",
            "input_boolean.hoymiles_tariff_charge_active",
            "input_boolean.hoymiles_rce_discharge_active",
        ):
            assert automatic_owner in block[release_wait:mode_position]
        assert release_wait < mode_position < readback_position < timer_position
        assert '- delay: "00:00:05"' in block[mode_position:timer_position]
        assert "continue_on_timeout: false" in block[mode_position:timer_position]

    explicit_stop = scheduler.split(
        "hoymiles_stop_scheduled_cycle:", 1
    )[1].split("hoymiles_rollback_tariff_transaction:", 1)[0]
    cancel_position = explicit_stop.index("action: timer.cancel")
    mode_position = explicit_stop.index('option: "self_use"')
    readback_position = explicit_stop.rindex('state: "self_use"')
    clear_position = explicit_stop.rindex("action: input_boolean.turn_off")
    assert cancel_position < mode_position < readback_position < clear_position
    assert "timer.hoymiles_discharge\n        state: \"idle\"" in explicit_stop
    assert "timer.hoymiles_charge\n        state: \"idle\"" in explicit_stop
    assert "continue_on_timeout: false" in explicit_stop

    # The balancing handover cancels timers while its own owner blocks normal
    # finish automations. It must close manual owners itself, but only after
    # exact Self-Use and both idle timer readbacks.
    balancing = scheduler.split(
        "hoymiles_start_battery_balancing:", 1
    )[1].split("hoymiles_stop_battery_balancing:", 1)[0]
    handover_clear = balancing.split(
        "The balancing owner intentionally blocks the normal manual finalizers",
        1,
    )[1]
    for marker in (
        'state: "self_use"',
        "timer.hoymiles_discharge",
        "timer.hoymiles_charge",
        'state: "idle"',
        "input_boolean.hoymiles_discharge_cycle_active",
        "input_boolean.hoymiles_charge_cycle_active",
    ):
        assert marker in handover_clear
    assert handover_clear.index('state: "self_use"') < handover_clear.index(
        "action: input_boolean.turn_off"
    )

    finish_specs = (
        (
            "id: hoymiles_finish_grid_discharge",
            "id: hoymiles_finish_grid_charge",
            "input_boolean.hoymiles_discharge_cycle_active",
            "timer.hoymiles_discharge",
        ),
        (
            "id: hoymiles_finish_grid_charge",
            "id: hoymiles_restore_ems_cycle_after_ha_restart",
            "input_boolean.hoymiles_charge_cycle_active",
            "timer.hoymiles_charge",
        ),
    )
    positive_self_use = (
        "{{ is_state('sensor.hoymiles_ems_hardware_mode', 'self_use') }}"
    )
    for start_marker, end_marker, owner, timer in finish_specs:
        block = scheduler.split(start_marker, 1)[1].split(end_marker, 1)[0]
        for marker in (
            'minutes: "/1"',
            "id: timer_finished",
            "id: watchdog",
            "trigger.id == 'timer_finished'",
            ".last_changed",
            ">= 60",
            owner,
            timer,
            f"{{{{ is_state('{timer}', 'idle') }}}}",
            'timeout: "00:00:10"',
            'timeout: "00:00:35"',
            '- delay: "00:00:05"',
            positive_self_use,
            'state: "self_use"',
            "# Clear ownership only after positive readback",
        ):
            assert marker in block, (
                f"Manual finalization block {start_marker} lacks {marker}"
            )
        assert block.count("continue_on_timeout: false") >= 2
        clear_position = block.rindex("action: input_boolean.turn_off")
        assert block.index(positive_self_use) < clear_position
        assert block.rindex('state: "self_use"') < clear_position
        assert block.rindex(
            f"{{{{ is_state('{timer}', 'idle') }}}}"
        ) < clear_position

    restart = scheduler.split(
        "id: hoymiles_restore_ems_cycle_after_ha_restart",
        1,
    )[1].split("id: hoymiles_rcm_voltage_charge_control", 1)[0]
    restore_choices = restart.split("      - choose:", 1)[1]
    discharge_restore, charge_restore = restore_choices.split(
        "      - choose:", 1
    )
    for block, owner, timer, active_mode in (
        (
            discharge_restore,
            "input_boolean.hoymiles_discharge_cycle_active",
            "timer.hoymiles_discharge",
            "grid_discharge",
        ),
        (
            charge_restore,
            "input_boolean.hoymiles_charge_cycle_active",
            "timer.hoymiles_charge",
            "grid_charge",
        ),
    ):
        for marker in (
            owner,
            timer,
            f"{{{{ is_state('sensor.hoymiles_ems_hardware_mode', '{active_mode}') }}}}",
            positive_self_use,
            f"{{{{ is_state('{timer}', 'idle') }}}}",
            '- delay: "00:00:05"',
            'state: "self_use"',
            "continue_on_timeout: false",
        ):
            assert marker in block, (
                f"Manual restart recovery for {active_mode} lacks {marker}"
            )
        clear_position = block.rindex("action: input_boolean.turn_off")
        assert block.rindex(positive_self_use) < clear_position
        assert block.rindex('state: "self_use"') < clear_position
        assert block.rindex(
            f"{{{{ is_state('{timer}', 'idle') }}}}"
        ) < clear_position

    # A restored discharge timer is cancelled and finalized if the export
    # lockout now forbids its continuation.
    assert "binary_sensor.hoymiles_sale_block_active" in discharge_restore
    assert "action: timer.cancel" in discharge_restore

    def watchdog_eligible(
        owner: bool,
        timer_state: str,
        owner_age_seconds: float,
        timer_finished: bool = False,
    ) -> bool:
        return (
            owner
            and timer_state == "idle"
            and (timer_finished or owner_age_seconds >= 60.0)
        )

    assert watchdog_eligible(True, "idle", 0.0, timer_finished=True)
    assert not watchdog_eligible(True, "idle", 59.9)
    assert watchdog_eligible(True, "idle", 60.0)
    for non_idle in ("active", "paused", "unknown", "unavailable"):
        assert not watchdog_eligible(True, non_idle, 600.0)
    assert not watchdog_eligible(False, "idle", 600.0)

    def ownership_can_clear(owner: bool, timer_state: str, mode: str) -> bool:
        return owner and timer_state == "idle" and mode == "self_use"

    assert ownership_can_clear(True, "idle", "self_use")
    for unsafe_mode in (
        "unknown",
        "unavailable",
        "none",
        "grid_charge",
        "grid_discharge",
    ):
        assert not ownership_can_clear(True, "idle", unsafe_mode)
    for non_idle in ("active", "paused", "unknown", "unavailable"):
        assert not ownership_can_clear(True, non_idle, "self_use")
    assert not ownership_can_clear(False, "idle", "self_use")


def assert_tariff_startup_contracts() -> None:
    """Protect late-Solcast startup and fail-closed tariff readiness."""

    scheduler = (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml"
    ).read_text(encoding="utf-8")
    ready_block = scheduler.split(
        "unique_id: hoymiles_tariff_control_data_ready",
        1,
    )[1].split("attributes:", 1)[0]
    valid_statuses = {
        "ready",
        "insufficient_cheap_window",
        "no_charge_needed",
        "no_discount_window",
        "not_economically_beneficial",
        "shortage_in_low_period",
        "no_cheap_window",
    }
    blocked_statuses = {
        "missing_data",
        "optimizer_error",
        "unsupported_profile",
        "expired_profile",
        "soc_limits_conflict",
        "hard_reserve_unavailable",
    }
    for status in valid_statuses:
        assert f"'{status}'" in ready_block, (
            f"Valid tariff status {status} incorrectly blocks data readiness"
        )
    for status in blocked_statuses:
        assert f"'{status}'" not in ready_block, (
            f"Unsafe tariff status {status} incorrectly enables data readiness"
        )
    assert "'control_inputs_fresh') is sameas true" in ready_block, (
        "Tariff data readiness does not fail closed on stale SOC/BMS/LOAD"
    )

    sensor_source = (
        ROOT
        / "custom_components"
        / "hoymiles_hit_modbus"
        / "tariff_sensor.py"
    ).read_text(encoding="utf-8")
    retry_block = sensor_source.split(
        "def _async_input_changed",
        1,
    )[1].split("def _async_debounced_recalculate", 1)[0]
    assert "not self._forecast_retry_pending" in retry_block
    assert "self._forecast_accuracy_source_available()" in retry_block
    assert "self._forecast_retry_pending = True" in retry_block
    retry_method = sensor_source.split(
        "async def _async_retry_initial_forecast_accuracy",
        1,
    )[1].split("def _recalculate_and_write", 1)[0]
    assert "finally:" in retry_method
    assert "self._forecast_retry_pending = False" in retry_method

    for attribute in (
        "forecast_day_3_kwh",
        "forecast_day_3_raw_kwh",
    ):
        day_3_block = sensor_source.split(f'"{attribute}": (', 1)[1][:220]
        assert "if day_3_state is not None" in day_3_block
        assert "else None" in day_3_block, (
            f"Missing Day 3 must remain unknown for {attribute}, not 0 kWh"
        )


def assert_tariff_execution_contracts() -> None:
    """Keep optional grid support quiet without delaying required charging."""

    scheduler = (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml"
    ).read_text(encoding="utf-8")
    planned_slot_block = scheduler.split(
        "unique_id: hoymiles_tariff_planned_charge_slot",
        1,
    )[1].split(
        "unique_id: hoymiles_rce_reserve_ready",
        1,
    )[0]
    assert "not in ['ready', 'insufficient_cheap_window']" in planned_slot_block
    assert "current_run_remaining_minutes" in planned_slot_block
    assert "current_run_start_eligible" in planned_slot_block
    assert "(remaining | float(0)) < 7" in planned_slot_block
    start_block = scheduler.split(
        "# Start: plan i wszystkie dane są ważne",
        1,
    )[1].split(
        "# Aktualizacja ograniczeń BMS podczas bieżącego bloku",
        1,
    )[0]

    # Optional direct grid support must pass the optimizer's whole-run gate
    # and a separately tracked, stable intent. This must not be the old broad
    # plan fingerprint, which can drift with forecasts and target SOC.
    assert "'current_run_start_eligible'" in start_block
    assert "'current_run_intent_stable_seconds'" in start_block
    assert ">= 120" in start_block
    assert "'plan_stable_for_minutes'" not in start_block
    assert "'reserve_soc_percent'" in start_block
    assert "'input_number.hoymiles_tariff_maximum_soc'" in start_block
    assert "'current_run_remaining_minutes'" in scheduler

    # Required battery charging uses a capacity-scaled energy hysteresis. An
    # urgent reserve recovery bypasses only this headroom threshold.
    assert "['battery_charge', 'grid_support_and_charge']" in start_block
    assert "minimum_headroom_kwh" in start_block
    assert "(capacity | float(0)) * 0.005" in start_block
    assert "0.20] | max, 0.50]" in start_block
    assert "(soc | float(0)) < (reserve | float(0))" in start_block
    assert start_block.count(
        "'input_number.hoymiles_tariff_maximum_soc'"
    ) >= 2
    assert start_block.count(">= (reserve | float(0))") >= 1

    def eligible_required_charge(
        capacity_kwh: float,
        soc: float,
        target: float,
        reserve: float,
    ) -> bool:
        threshold = min(max(capacity_kwh * 0.005, 0.20), 0.50)
        headroom = (target - soc) * capacity_kwh / 100.0
        reserve_recovery = (
            soc < reserve
            and target - soc + EPSILON >= 0.2
            and headroom + EPSILON >= 0.10
        )
        return target > soc and (
            reserve_recovery or headroom + EPSILON >= threshold
        )

    for capacity_kwh in (10.0, 21.0, 230.0):
        threshold = min(max(capacity_kwh * 0.005, 0.20), 0.50)
        equality_target = 50.0 + threshold / capacity_kwh * 100.0
        assert eligible_required_charge(
            capacity_kwh, 50.0, equality_target, 25.0
        )
        assert not eligible_required_charge(
            capacity_kwh, 50.0, equality_target - 0.01, 25.0
        )
        assert eligible_required_charge(
            capacity_kwh, 24.9, 24.91, 25.0
        ) is False, "Tiny reserve deficits must not create a start/stop loop"
        assert eligible_required_charge(
            capacity_kwh,
            24.8,
            max(25.0, 24.8 + 0.10 / capacity_kwh * 100.0),
            25.0,
        ), "Material reserve recovery must bypass normal energy hysteresis"

    # Freeze the accepted intent before any Modbus acknowledgement wait. A
    # sensor refresh during the transaction may not change cycle ownership.
    assert "tariff_start_action:" in start_block
    assert "tariff_start_target_soc:" in start_block
    assert "tariff_start_reserve_soc:" in start_block
    assert "tariff_start_maximum_soc:" in start_block
    assert "tariff_start_power:" in start_block
    assert "tariff_start_slot_end:" in start_block
    assert "{{ tariff_start_action | default('none', true) }}" in start_block
    assert "{{ as_timestamp(tariff_start_slot_end) }}" in start_block
    assert start_block.count("tariff_start_power | float(0)") >= 5
    assert "Warunki bezpiecznego startu Grid Charge wygasły" in start_block
    assert "'current_zone') == 'low'" in start_block
    assert "- as_timestamp(now()) >= 300" in start_block
    assert "- as_timestamp(now())\n                       >= 420" in start_block
    assert '# The select entity may publish optimistically.' in start_block
    assert '- delay: "00:00:05"' in start_block
    final_guard = start_block.split(
        "# Setting readbacks may take up to two minutes",
        1,
    )[1].split(
        "# The select entity may publish optimistically",
        1,
    )[0]
    for marker in (
        "input_boolean.hoymiles_tariff_charge_enabled",
        "input_boolean.hoymiles_tariff_charge_active",
        "input_text.hoymiles_tariff_active_action",
        "input_boolean.hoymiles_rce_discharge_enabled",
        "input_boolean.hoymiles_rcm_enabled",
        "input_boolean.hoymiles_battery_balancing_active",
        "input_boolean.hoymiles_discharge_cycle_active",
        "input_boolean.hoymiles_charge_cycle_active",
        "timer.hoymiles_discharge",
        "timer.hoymiles_charge",
        "binary_sensor.hoymiles_ems_execution_ready",
        "binary_sensor.hoymiles_tariff_control_data_ready",
        "input_number.hoymiles_tariff_maximum_soc",
        "tariff_start_reserve_soc",
        "tariff_start_action",
        "control_inputs_fresh",
        "current_slot_planned",
        "current_action",
        "soc_age_seconds",
        "bms_charge_age_seconds",
        "plan_age >= 0 and plan_age <= 120",
    ):
        assert marker in final_guard, f"Final Grid Charge guard lacks {marker}"
    assert ") >= tariff_start_reserve_soc" in final_guard
    assert "tariff_start_maximum_soc" not in final_guard

    # The automatic start is a durable transaction: snapshot and frozen action
    # precede ownership, and ownership precedes the first shared register write.
    snapshot_position = start_block.index(
        "input_number.hoymiles_tariff_saved_max_charge_power"
    )
    action_position = start_block.index(
        "entity_id: input_text.hoymiles_tariff_active_action"
    )
    owner_position = start_block.index(
        "entity_id: input_boolean.hoymiles_tariff_charge_active",
        action_position,
    )
    first_register_write = start_block.index(
        "action: script.hoymiles_verified_set_ems_maximum_charge_power"
    )
    assert snapshot_position < action_position < owner_position < first_register_write
    for marker in (
        "input_number.hoymiles_tariff_saved_force_charge_soc",
        "script.hoymiles_rollback_tariff_transaction",
    ):
        assert marker in start_block

    rollback = scheduler.split(
        "hoymiles_rollback_tariff_transaction:", 1
    )[1].split("hoymiles_rollback_rce_transaction:", 1)[0]
    for marker in (
        'state: "self_use"',
        "input_number.hoymiles_tariff_saved_max_charge_power",
        "input_number.hoymiles_tariff_saved_force_charge_soc",
        "sensor.hoymiles_hit_ems_maximum_charge_power_readback",
        "sensor.hoymiles_hit_ems_force_charge_soc_readback",
        "continue_on_timeout: false",
    ):
        assert marker in rollback, f"Tariff rollback lacks {marker}"
    assert rollback.rindex('state: "self_use"') < rollback.rindex(
        "action: input_boolean.turn_off"
    )
    assert rollback.rindex(
        "input_number.hoymiles_tariff_saved_force_charge_soc"
    ) < rollback.rindex("action: input_boolean.turn_off")

    update_block = scheduler.split(
        "# Aktualizacja ograniczeń BMS podczas bieżącego bloku",
        1,
    )[1].split(
        "# Stop: koniec zapamiętanego ciągłego okna",
        1,
    )[0]
    assert "active == 'grid_support'" in update_block
    assert "current == 'grid_support'" in update_block
    assert update_block.count(
        "['battery_charge', 'grid_support_and_charge']"
    ) >= 2
    assert "A lower maximum SOC chosen during an active" in scheduler
    assert "input_number.hoymiles_tariff_maximum_soc" in scheduler.split(
        "id: hoymiles_tariff_grid_charge_control", 1
    )[1].split("actions:", 1)[0]
    stop_block = scheduler.split(
        "# Stop: koniec zapamiętanego ciągłego okna",
        1,
    )[1].split("id: hoymiles_rce_grid_discharge_control", 1)[0]
    assert "'current_run_continue_eligible')" in stop_block
    assert "is not sameas true" in stop_block
    assert "'current_run_start_eligible') is not sameas true" not in stop_block
    assert "'current_run_intent_stable_seconds') | float(0))" in stop_block
    assert "'input_number.hoymiles_tariff_maximum_soc'" in stop_block
    for marker in (
        "'no_charge_needed'",
        "'soc_limits_conflict'",
        "'hard_reserve_unavailable'",
        "'bms_charge_data_fresh'",
        "'bms_charge_available'",
        "is not sameas true",
    ):
        assert marker in stop_block, f"Tariff hard stop lacks {marker}"
    stabilized_stop = stop_block.split(
        "current_run_intent_stable_seconds", 1
    )[1]
    assert "current_run_continue_eligible" in stabilized_stop
    assert "== 'grid_support'" not in stabilized_stop, (
        "Required-charge runs ignore a stable withdrawn plan"
    )
    tariff_choose = scheduler.split(
        "id: hoymiles_tariff_grid_charge_control", 1
    )[1].split("id: hoymiles_rce_grid_discharge_control", 1)[0]
    conflict_pos = tariff_choose.index(
        "A value below the protected reserve first"
    )
    clamp_pos = tariff_choose.index(
        "and (maximum | float(0)) < (latched | float(0))"
    )
    update_pos = tariff_choose.index(
        "# Aktualizacja ograniczeń BMS podczas bieżącego bloku"
    )
    assert conflict_pos < clamp_pos < update_pos, (
        "Maximum-SOC conflict handling is shadowed by a later choose branch"
    )
    assert "current_run_continue_eligible') is not sameas true" in update_block
    continue_gate = update_block.split(
        "current_run_continue_eligible", 1
    )[0].rsplit("{{ not (", 1)[-1]
    assert "== 'grid_support'" not in continue_gate
    assert "current_run_start_eligible') is not sameas true" not in update_block
    assert "current_run_intent_stable_seconds') | float(0)) >= 120" in update_block

    # Pure grid support must set an integer Force Charge target strictly below
    # the starting SOC, at/below maximum SOC and not below the protected floor.
    assert "tariff_start_soc | round(0, 'floor')) - 1" in start_block
    assert "tariff_start_maximum_soc" in start_block
    assert "tariff_start_reserve_soc" in start_block
    assert "[[[below_live, maximum] | min, reserve] | max, 0]" in start_block
    old_grid_support_target = (
        "states('sensor.hoymiles_hit_overview_battery_soc')\n"
        "                         | float(0) | round(0, 'floor')"
    )
    assert old_grid_support_target not in start_block

    sensor_source = (
        ROOT
        / "custom_components"
        / "hoymiles_hit_modbus"
        / "tariff_sensor.py"
    ).read_text(encoding="utf-8")
    for attribute in (
        "current_run_start_eligible",
        "current_run_suppression_reason",
        "current_run_continue_eligible",
        "current_run_continue_reason",
        "current_run_grid_import_kwh",
        "current_run_direct_load_kwh",
        "current_run_stored_kwh",
        "current_run_benefit_pln",
        "current_run_remaining_minutes",
        "current_run_intent_stable_seconds",
        "command_charge_power_percent",
        "modeled_effective_charge_power_percent",
    ):
        assert f'"{attribute}"' in sensor_source, (
            f"Tariff scheduler/sensor execution contract lacks {attribute}"
        )
    assert "effective_charge_power_percent" in sensor_source, (
        "The public legacy command alias disappeared during v1.5.2"
    )
    tariff_control = scheduler.split(
        "id: hoymiles_tariff_grid_charge_control",
        1,
    )[1].split("id: hoymiles_rce_grid_discharge_control", 1)[0]
    assert "command_charge_power_percent" in tariff_control
    assert "effective_charge_power_percent" not in tariff_control, (
        "The scheduler must consume the explicit command, not the modeled "
        "delivered-power diagnostic"
    )

    intent_block = sensor_source.split(
        "current_run_intent = (",
        1,
    )[1].split(
        "if current_run_intent !=",
        1,
    )[0]
    assert "result.current_run_start_eligible" in intent_block, (
        "Grid-support eligibility changes do not reset intent stability"
    )
    assert "result.current_run_suppression_reason" in intent_block, (
        "Grid-support suppression changes do not reset intent stability"
    )

    optimizer_source = (
        ROOT
        / "custom_components"
        / "hoymiles_hit_modbus"
        / "tariff_optimizer.py"
    ).read_text(encoding="utf-8")
    continue_block = optimizer_source.split(
        "# Continuing an already active support run",
        1,
    )[1].split(
        "        live_values = (",
        1,
    )[0]
    assert "settings.current_load_power_kw" in continue_block
    assert "settings.current_pv_power_kw" in continue_block
    assert "settings.current_battery_power_kw" not in continue_block, (
        "Zero battery discharge after entering Grid Charge must not abort support"
    )
    assert 'current_run_continue_reason = "live_data_missing"' in continue_block
    assert 'current_run_continue_reason = "pv_covers_load"' in continue_block
    assert 'current_run_continue_reason = "eligible"' in continue_block


def assert_rce_execution_contracts() -> None:
    """Protect atomic RCE start and a monotonic accepted discharge block."""

    scheduler = (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml"
    ).read_text(encoding="utf-8")
    block = scheduler.split(
        "id: hoymiles_rce_grid_discharge_control",
        1,
    )[1].split(
        "id: hoymiles_automatic_ems_control_failsafe",
        1,
    )[0]
    start = block.split("# Start RCE:", 1)[1].split("# Stop RCE:", 1)[0]
    stop = block.split("# Stop RCE:", 1)[1]

    for attribute in (
        "current_slot_planned",
        "current_slot_end",
        "current_run_end",
        "current_slot_remaining_minutes",
        "current_slot_start_eligible",
        "current_required_minimum_soc_percent",
    ):
        assert f"'{attribute}'" in block, (
            f"RCE scheduler does not consume {attribute}"
        )
    power_template = scheduler.split(
        'name: "Hoymiles RCE Effective Discharge Power Percent"', 1
    )[1].split('name: "Hoymiles RCE Revenue Rate"', 1)[0]
    for marker in (
        "current_slot_execution_power_percent",
        "current_slot_execution_export_power_kw",
        "current_slot_execution_discharge_power_kw",
        "[requested, bms_percent, planned, 100] | min",
    ):
        assert marker in power_template, (
            f"RCE execution power is not bounded by the plan: {marker}"
        )
    for marker in (
        "input_datetime.hoymiles_rce_latched_slot_end",
        "input_number.hoymiles_rce_latched_minimum_soc",
        "# The Force Discharge floor is monotonic",
        "[latched, register,",
        "| max | round(0, 'ceil')",
    ):
        assert marker in block, f"RCE monotonic latch lacks {marker}"
    assert "end > as_timestamp(states(" in block, (
        "A live refresh can shorten the accepted RCE deadline"
    )
    assert "current_slot_planned" in start
    assert "current_slot_start_eligible" in start
    assert "current_slot_remaining_minutes" in start
    assert "# Final, fresh interlock" in start
    assert "last_reported" in start
    for marker in (
        "rce_start_run_end",
        "plan_age >= 0 and plan_age <= 120",
        "soc_age >= 0 and soc_age <= 120",
        "floor_age >= 0 and floor_age <= 120",
        "fresh_required",
        "sensor.hoymiles_hit_ems_force_discharge_soc_readback",
        "sensor.hoymiles_hit_overview_battery_soc",
    ):
        assert marker in start, f"RCE fresh start interlock lacks {marker}"
    assert "current_run_end" in start
    rce_mode_command = start.split(
        "# Ownership is already active, so the first export sample",
        1,
    )[1].split("- wait_template:", 1)[0]
    assert "not is_state(" in rce_mode_command
    assert "'sensor.hoymiles_ems_hardware_mode', 'grid_discharge'" in rce_mode_command
    assert "hoymiles_hit_rcm_voltage_plan" not in rce_mode_command, (
        "RCE mode start accidentally depends on the RCEm pre-discharge plan"
    )
    assert "pre_discharge_" not in rce_mode_command
    assert "current_slot_end')" not in block.split(
        "# Latch the complete contiguous run", 1
    )[1].split("# The Force Discharge floor", 1)[0]
    assert "binary_sensor.hoymiles_ems_export_allowed" in start
    assert "rce_start_minimum_soc" in start
    assert "[required, register] | max" in start
    assert "input_number.hoymiles_rce_latched_minimum_soc" in stop
    assert "sensor.hoymiles_rce_effective_minimum_soc" not in stop
    assert "as_timestamp(now()) >= as_timestamp(states(" in stop
    assert "binary_sensor.hoymiles_rce_price_above_threshold" not in stop
    for marker in (
        "current_slot_continue_eligible",
        "current_slot_continue_stable_seconds",
        ">= 60",
        "current_price_pln_kwh",
        "automatic_price_floor_pln_kwh",
    ):
        assert marker in stop, f"RCE active stop lacks {marker}"
    ready = scheduler.split(
        'name: "Hoymiles RCE Control Data Ready"', 1
    )[1].split('name: "Hoymiles EMS Export Allowed"', 1)[0]
    for marker in (
        "rce_today_data_fresh",
        "forecast_today_data_fresh",
        "gcf_execution_data_fresh",
        "is sameas true",
    ):
        assert marker in ready, f"RCE execution freshness lacks {marker}"
    assert "input_boolean.hoymiles_rcm_export_control_active" in start, (
        "RCE start ignores an outstanding RCEm export-register owner"
    )

    # Inactive RCE does not pre-write discharge power. Start snapshots both
    # shared registers and exposes ownership before its first Modbus write.
    before_start = block.split("# Start RCE:", 1)[0]
    assert "Inactive RCE never pre-writes a shared register" in before_start
    snapshot_position = start.index(
        "input_number.hoymiles_rce_saved_max_discharge_power"
    )
    owner_position = start.index(
        "entity_id: input_boolean.hoymiles_rce_discharge_active",
        snapshot_position,
    )
    first_register_write = start.index(
        "action: script.hoymiles_verified_set_ems_maximum_discharge_power"
    )
    assert snapshot_position < owner_position < first_register_write
    for marker in (
        "input_number.hoymiles_rce_saved_force_discharge_soc",
        "script.hoymiles_rollback_rce_transaction",
        "rce_start_power",
    ):
        assert marker in start

    rollback = scheduler.split(
        "hoymiles_rollback_rce_transaction:", 1
    )[1].split("hoymiles_start_battery_balancing:", 1)[0]
    for marker in (
        'state: "self_use"',
        "input_number.hoymiles_rce_saved_max_discharge_power",
        "input_number.hoymiles_rce_saved_force_discharge_soc",
        "sensor.hoymiles_hit_ems_maximum_discharge_power_readback",
        "sensor.hoymiles_hit_ems_force_discharge_soc_readback",
        "continue_on_timeout: false",
    ):
        assert marker in rollback, f"RCE rollback lacks {marker}"
    assert rollback.rindex('state: "self_use"') < rollback.rindex(
        "action: input_boolean.turn_off"
    )
    assert rollback.rindex(
        "input_number.hoymiles_rce_saved_force_discharge_soc"
    ) < rollback.rindex("action: input_boolean.turn_off")
    sync = scheduler.split(
        "id: hoymiles_rce_sync_dynamic_minimum_soc",
        1,
    )[1].split(
        "id: hoymiles_enforce_sale_block",
        1,
    )[0]
    assert "input_number.hoymiles_rce_latched_minimum_soc" in sync
    assert sync.count("[dynamic, latched, current] | max") >= 2, (
        "Background dynamic-SOC sync can lower an active RCE floor"
    )

    # These are diagnostic parts of the same sensor contract even though the
    # actuator needs only eligibility, deadline and the protected SOC.
    sensor = (
        ROOT
        / "custom_components"
        / "hoymiles_hit_modbus"
        / "rce_sensor.py"
    ).read_text(encoding="utf-8")
    for attribute in (
        "current_run_end",
        "current_slot_fraction",
        "current_slot_planned_export_kwh",
        "current_slot_suppression_reason",
    ):
        assert f'"{attribute}"' in sensor

    def monotonic_floor(latched: float, register: float, required: float) -> int:
        return ceil(max(latched, register, required))

    assert monotonic_floor(24.0, 25.0, 31.2) == 32
    assert monotonic_floor(32.0, 25.0, 21.0) == 32

    # A contiguous transaction deadline is monotonic even across the repeated
    # local hour at the end of daylight-saving time.
    first_end = datetime(2026, 10, 25, 2, 30, tzinfo=WARSAW, fold=0)
    second_end = datetime(2026, 10, 25, 2, 30, tzinfo=WARSAW, fold=1)
    assert second_end.timestamp() > first_end.timestamp()
    assert max(first_end.timestamp(), second_end.timestamp()) == second_end.timestamp()


def assert_rcm_execution_contracts() -> None:
    """Protect emergency priority and atomic/frozen RCEm pre-discharge."""

    scheduler = (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml"
    ).read_text(encoding="utf-8")
    main = scheduler.split(
        "id: hoymiles_rcm_voltage_charge_control",
        1,
    )[1].split(
        "id: hoymiles_rcm_pre_discharge_control",
        1,
    )[0]
    assert "mode: restart" in main.split("actions:", 1)[0], (
        "RCEm emergency cannot interrupt a normal Modbus readback wait"
    )
    helper_block = scheduler.split("script:", 1)[1].split(
        "hoymiles_start_grid_discharge:", 1
    )[0]
    for helper in (
        "hoymiles_verified_set_ems_mode",
        "hoymiles_verified_set_ems_maximum_charge_power",
        "hoymiles_verified_set_ems_force_charge_soc",
        "hoymiles_verified_set_ems_maximum_discharge_power",
        "hoymiles_verified_set_ems_force_discharge_soc",
        "hoymiles_verified_set_battery_max_charge_power",
        "hoymiles_verified_set_gcf_export_limit",
    ):
        body = helper_block.split(f"  {helper}:", 1)[1].split("\n  hoymiles_", 1)[0]
        assert "mode: restart" in body
        assert f"action: script.{helper}" not in body, f"{helper} calls itself"
        assert "readback_generation" in body
        assert "generation_after_write" in body
        assert body.index("action: number.set_value") < body.index(
            "generation_after_write"
        ) if helper != "hoymiles_verified_set_ems_mode" else (
            body.index("action: select.select_option")
            < body.index("generation_after_write")
        )
        assert "ems_verified_hardware_readback_supported" in body
    pre = scheduler.split(
        "id: hoymiles_rcm_pre_discharge_control",
        1,
    )[1]

    emergency_pos = main.index("# Napięcie >=253 V")
    release_pos = main.index("# Zwolnienie własności")
    assert emergency_pos < release_pos, "RCEm emergency is shadowed by release"
    emergency = main[emergency_pos:release_pos]
    for marker in (
        "live_emergency",
        "emergency_action_ready",
        "maximum_voltage >= 253.0",
        "emergency_voltage_data_fresh",
        "charge_actuator_data_fresh",
        "export_actuator_data_fresh",
        "emergency_charge_path",
        "emergency_export_path",
        "# Final emergency interlocks",
    ):
        assert marker in emergency, f"RCEm emergency path lacks {marker}"
    assert "'forecast_data_fresh'" not in emergency
    assert "'history_data_fresh'" not in emergency
    assert "'actuator_data_fresh'" not in emergency
    assert "plan_status" not in emergency
    assert "and voltage_data_fresh" not in emergency, (
        "A missing non-emergency phase still blocks the live 253 V path"
    )
    ownership_setup = emergency.split(
        "# Charge and export ownership are independent",
        1,
    )[1].split("# A zero/stale BMS charge limit", 1)[0]
    assert "emergency_charge_path" in ownership_setup
    assert "input_number.hoymiles_rcm_saved_battery_charge_power" in ownership_setup
    assert "input_boolean.hoymiles_rcm_active" in ownership_setup
    assert ownership_setup.index("emergency_charge_path") < ownership_setup.index(
        "input_number.hoymiles_rcm_saved_battery_charge_power"
    )
    charge_path = main.split("emergency_charge_path: >-", 1)[1].split(
        "emergency_export_path:", 1
    )[0]
    assert "bms_charge_available" in charge_path
    assert "bms_charge_data_fresh" in charge_path
    assert "ems_mode_data_fresh" in charge_path
    assert "recommended_limit >= 10" in charge_path
    export_available = main.split("export_control_available: >-", 1)[1].split(
        "rcm_blocked:", 1
    )[0]
    assert "| float(-1)) >= 0" in export_available
    assert "| float(0)) > 0" not in export_available, (
        "A held 0% export limit must remain an available emergency actuator"
    )
    restore_export = main.split(
        "Odtwórz limit eksportu także po restarcie", 1
    )[1].split("Normalny regulator zaczyna", 1)[0]
    assert "export_register_data_fresh" in restore_export
    assert "export_actuator_data_fresh" not in restore_export, (
        "RCEm export rollback still depends on the execution/GCF path"
    )

    normal_start = main.split(
        "# Normalny regulator zaczyna",
        1,
    )[1].split(
        "# Po przejęciu własności",
        1,
    )[0]
    for marker in (
        "prediction_ready",
        "voltage_data_fresh",
        "charge_actuator_data_fresh",
        "forecast_data_fresh",
        "history_data_fresh",
        "live_power_data_fresh",
        "bms_charge_data_fresh",
        "bms_charge_available",
        "system_power_data_valid",
        "binary_sensor.hoymiles_ems_execution_ready",
        "# Final normal-control interlock",
        "last_reported",
        "age >= 0 and age <= 60",
    ):
        assert marker in normal_start, f"Normal RCEm start lacks {marker}"
    assert normal_start.count("wait_template:") >= 2
    assert "Each transaction retains only its own durable owner" in emergency
    assert "above: 252.99" in main, (
        "RCEm emergency trigger must cross at the same 253 V policy boundary"
    )
    charge_path = main.split("emergency_charge_path: >-", 1)[1].split(
        "emergency_export_path: >-", 1
    )[0]
    assert "system_power_data_valid" in charge_path
    export_path = main.split("emergency_export_path: >-", 1)[1].split(
        "normal_export_path: >-", 1
    )[0]
    assert "ems_mode_data_fresh" not in export_path, (
        "Export-only emergency still depends on EMS mode telemetry"
    )
    assert "system_power_data_valid" not in export_path, (
        "A live export clamp must remain independent of kW-to-percent topology"
    )
    emergency_final = emergency.split("# Final emergency interlocks", 1)[1]
    charge_final, export_final = emergency_final.split(
        "# Export emergency finalization (independent actuator path).", 1
    )
    assert "# Charge emergency finalization (independent actuator path)." in charge_final
    assert "'sensor.hoymiles_ems_hardware_mode', 'self_use'" in charge_final
    assert "script.hoymiles_verified_set_battery_max_charge_power" in charge_final
    assert "input_boolean.hoymiles_rcm_active" in charge_final
    assert "script.hoymiles_verified_set_gcf_export_limit" not in charge_final
    assert "input_boolean.hoymiles_rcm_export_control_active" not in charge_final
    assert "script.hoymiles_verified_set_gcf_export_limit" in export_final
    assert "input_boolean.hoymiles_rcm_export_control_active" in export_final
    assert "script.hoymiles_verified_set_battery_max_charge_power" not in export_final
    assert "input_boolean.hoymiles_rcm_active" not in export_final
    for actuator_final in (charge_final, export_final):
        assert "continue_on_error: true" in actuator_final
        assert "continue_on_timeout: true" in actuator_final
    self_use_write = emergency.split(
        "# Charge and export ownership are independent", 1
    )[1].split("# A zero/stale BMS charge limit", 1)[0]
    assert "{{ emergency_charge_path" in self_use_write

    for marker in (
        "pre_discharge_start_eligible",
        "pre_discharge_continue_eligible",
        "pre_discharge_transaction_ready",
        "pre_discharge_deadline",
        "bms_discharge_available",
        "bms_discharge_data_fresh",
        "pre_discharge_actuator_data_fresh",
        "discharge_registers_data_fresh",
        "input_datetime.hoymiles_rcm_latched_pre_discharge_deadline",
        "input_number.hoymiles_rcm_latched_pre_discharge_target_soc",
        "input_number.hoymiles_rcm_latched_pre_discharge_power",
        "# Ownership is already active here",
    ):
        assert marker in pre, f"RCEm pre-discharge lacks {marker}"
    stop = pre.split("# Każde naruszenie warunków", 1)[1].split(
        "# Rozpoczęcie jest dozwolone",
        1,
    )[0]
    assert "not pre_discharge_continue_eligible" in stop
    assert "not voltage_data_fresh" in stop
    assert "not pre_discharge_actuator_data_fresh" in stop
    assert "not bms_discharge_data_fresh" in stop
    assert "not bms_discharge_available" in stop
    assert "maximum_voltage >= 248.4" in stop
    assert "forecast_data_fresh" not in stop
    assert "history_data_fresh" not in stop
    assert "Keep ownership until mode and both Modbus values" in stop
    assert stop.count("discharge_registers_data_fresh") >= 3

    start = pre.split("# Rozpoczęcie jest dozwolone", 1)[1]
    for marker in (
        "pre_discharge_start_eligible",
        "pre_discharge_transaction_ready",
        "prediction_ready",
        "forecast_data_fresh",
        "history_data_fresh",
        "live_power_data_fresh",
        "pre_discharge_actuator_data_fresh",
        "bms_discharge_available",
        "as_timestamp(pre_discharge_deadline, 0)",
    ):
        assert marker in start, f"RCEm pre-discharge start lacks {marker}"
    assert start.count("wait_template:") >= 3
    assert start.index("Ownership begins before the first Modbus write") < start.index(
        "action: script.hoymiles_verified_set_ems_maximum_discharge_power"
    )
    assert "A failed final interlock or mode readback is a pending restore" in start
    owned_phase = start.split("# Ownership begins before the first Modbus write", 1)[1]
    final_interlock = owned_phase.split(
        "# Ownership is already active here",
        1,
    )[1].split(
        "# A failed final interlock or mode readback",
        1,
    )[0]
    assert "pre_discharge_continue_eligible" in final_interlock
    assert "pre_discharge_start_eligible" not in final_interlock
    assert "pre_discharge_transaction_ready" not in final_interlock
    assert "prediction_ready" not in final_interlock
    assert "forecast_data_fresh" not in final_interlock
    assert "history_data_fresh" not in final_interlock
    for marker in (
        "input_datetime.hoymiles_rcm_latched_pre_discharge_deadline",
        "input_number.hoymiles_rcm_latched_pre_discharge_target_soc",
        "input_number.hoymiles_rcm_latched_pre_discharge_power",
        "sensor.hoymiles_hit_ems_maximum_discharge_power_readback",
        "sensor.hoymiles_hit_ems_force_discharge_soc_readback",
    ):
        assert marker in final_interlock, (
            f"Owned RCEm final interlock lacks frozen/readback guard {marker}"
        )
    post_mode_guard = owned_phase.split(
        "# A failed final interlock or mode readback",
        1,
    )[1].split("                then:", 1)[0]
    assert "pre_discharge_continue_eligible" in post_mode_guard
    assert "pre_discharge_start_eligible" not in post_mode_guard
    assert "pre_discharge_transaction_ready" not in post_mode_guard
    assert "'sensor.hoymiles_ems_hardware_mode', 'grid_discharge'" in post_mode_guard
    assert "# Podczas cyklu aktualizuj moc i cel SOC" not in pre, (
        "Active RCEm target still follows a moving forecast"
    )

    def emergency_paths(
        charge_fresh: bool,
        bms_charge_ok: bool,
        export_requested: bool,
        export_fresh: bool,
    ) -> tuple[bool, bool]:
        return (
            charge_fresh and bms_charge_ok,
            export_requested and export_fresh,
        )

    assert emergency_paths(False, True, True, True) == (False, True)
    assert emergency_paths(True, True, True, False) == (True, False)
    assert emergency_paths(False, True, True, False) == (False, False)

    def emergency_ack_outcome(
        charge_path: bool,
        charge_ack: bool,
        export_path: bool,
        export_ack: bool,
    ) -> tuple[bool, bool]:
        """Successful emergency actuators survive failure of the other path."""

        return charge_path and charge_ack, export_path and export_ack

    assert emergency_ack_outcome(True, False, True, True) == (False, True)
    assert emergency_ack_outcome(True, True, True, False) == (True, False)
    assert emergency_ack_outcome(True, True, True, True) == (True, True)

    def charge_ownership_transition(
        charge_path: bool,
        ownership_preexisting: bool,
    ) -> tuple[bool, bool]:
        snapshot_user_value = charge_path and not ownership_preexisting
        ownership_after_setup = ownership_preexisting or charge_path
        return snapshot_user_value, ownership_after_setup

    assert charge_ownership_transition(False, False) == (False, False)
    assert charge_ownership_transition(True, False) == (True, True)
    assert charge_ownership_transition(False, True) == (False, True)

    def export_actuator_available(
        numeric_limit: bool,
        current_limit_percent: float,
        generation_control_available: bool,
    ) -> bool:
        return (
            numeric_limit
            and current_limit_percent >= 0.0
            and generation_control_available
        )

    assert export_actuator_available(True, 0.0, True)
    assert export_actuator_available(True, 100.0, True)
    assert not export_actuator_available(False, 0.0, True)

    def pre_discharge_phase_eligible(
        ownership_active: bool,
        start_eligible: bool,
        continue_eligible: bool,
    ) -> bool:
        return continue_eligible if ownership_active else start_eligible

    assert pre_discharge_phase_eligible(False, True, False)
    assert pre_discharge_phase_eligible(True, False, True)
    assert not pre_discharge_phase_eligible(True, True, False)

    def ownership_can_clear(actuator_fresh: bool, readback_ok: bool) -> bool:
        return actuator_fresh and readback_ok

    assert ownership_can_clear(True, True)
    assert not ownership_can_clear(False, True)
    assert not ownership_can_clear(True, False)


def assert_physical_hardware_readback_contracts() -> None:
    """Forbid optimistic HA echoes from acknowledging a Modbus transaction."""

    scheduler = (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml"
    ).read_text(encoding="utf-8")
    helpers = scheduler.split("script:", 1)[1].split(
        "  hoymiles_start_grid_discharge:", 1
    )[0]
    helper_specs = {
        "hoymiles_verified_set_ems_mode": (
            "action: select.select_option",
            "ems_control_readback_generation",
            "ems_mode_readback_code",
        ),
        "hoymiles_verified_set_ems_maximum_charge_power": (
            "action: number.set_value",
            "ems_control_readback_generation",
            "ems_maximum_charge_power_readback",
        ),
        "hoymiles_verified_set_ems_force_charge_soc": (
            "action: number.set_value",
            "ems_control_readback_generation",
            "ems_force_charge_soc_readback",
        ),
        "hoymiles_verified_set_ems_maximum_discharge_power": (
            "action: number.set_value",
            "ems_control_readback_generation",
            "ems_maximum_discharge_power_readback",
        ),
        "hoymiles_verified_set_ems_force_discharge_soc": (
            "action: number.set_value",
            "ems_control_readback_generation",
            "ems_force_discharge_soc_readback",
        ),
        "hoymiles_verified_set_battery_max_charge_power": (
            "action: number.set_value",
            "battery_charge_power_readback_generation",
            "battery_max_charge_power_readback",
        ),
        "hoymiles_verified_set_gcf_export_limit": (
            "action: number.set_value",
            "gcf_control_readback_generation",
            "gcf_maximum_export_power_readback",
        ),
    }
    for helper, (service, generation, mirror) in helper_specs.items():
        body = helpers.split(f"  {helper}:", 1)[1].split("\n  hoymiles_", 1)[0]
        service_pos = body.index(service)
        capture_pos = body.index("generation_after_write")
        wait_pos = body.index("wait_template:", capture_pos)
        assert service_pos < capture_pos < wait_pos
        assert generation in body[capture_pos:]
        assert mirror in body[wait_pos:]
        assert "mode: restart" in body
        assert "sensor.hoymiles_hit_ems_verified_hardware_readback_supported" in body
        assert "| float(0) > 0.5" in body[wait_pos:]
        assert f"action: script.{helper}" not in body

    mode = helpers.split("  hoymiles_verified_set_ems_mode:", 1)[1].split(
        "\n  hoymiles_verified_set_ems_maximum_charge_power:", 1
    )[0]
    for mirror in (
        "ems_mode_readback_code",
        "ems_self_use_soc_readback",
        "ems_backup_soc_readback",
        "ems_force_charge_soc_readback",
        "ems_maximum_charge_power_readback",
        "ems_force_discharge_soc_readback",
        "ems_maximum_discharge_power_readback",
    ):
        assert mirror in mode
    assert "'self_use': 0" in mode and "'grid_charge': 4" in mode
    assert "'grid_discharge': 5" in mode

    hardware_mode = scheduler.split(
        '- name: "Hoymiles EMS Hardware Mode"', 1
    )[1].split('- name: "Hoymiles Actual Load Power"', 1)[0]
    for marker in (
        "sensor.hoymiles_hit_ems_mode_readback_code",
        "sensor.hoymiles_hit_ems_control_readback_generation",
        "sensor.hoymiles_hit_ems_verified_hardware_readback_supported",
        "{0: 'self_use', 4: 'grid_charge'",
        "5: 'grid_discharge'",
    ):
        assert marker in hardware_mode

    # The writable select is a command transport, never an acknowledgement.
    # It may occur only as the verified mode helper's service target; all
    # policy reads and triggers consume the physical-code-derived alias.
    writable_mode = "select.hoymiles_hit_ems_mode"
    assert scheduler.count(writable_mode) == 1
    assert f"entity_id: {writable_mode}" in mode

    # No production path may call a writable HIT entity directly.  The only
    # direct services are inside the seven verified helpers above.
    production = scheduler.split("  hoymiles_start_grid_discharge:", 1)[1]
    assert "action: number.set_value" not in production
    assert "action: select.select_option" not in production
    assert writable_mode not in production
    assert "sensor.hoymiles_ems_hardware_mode" in production

    execution_ready = scheduler.split(
        '- name: "Hoymiles EMS Execution Ready"', 1
    )[1].split('- name: "Hoymiles RCE Control Data Ready"', 1)[0]
    for marker in (
        "ems_verified_hardware_readback_supported",
        "ems_mode_readback_code",
        "ems_control_readback_generation",
        "parallel_topology_readback_generation",
        "ems_age >= -5",
        "topology_age >= -5",
    ):
        assert marker in execution_ready
    assert "parallel_ems_control_status" not in execution_ready

    export_allowed = scheduler.split(
        '- name: "Hoymiles EMS Export Allowed"', 1
    )[1].split('- name: "Hoymiles Tariff Control Data Ready"', 1)[0]
    assert "gcf_enable_readback_code" in export_allowed
    assert "gcf_maximum_export_power_readback" in export_allowed
    assert "gcf_control_readback_generation" in export_allowed
    assert "select.hoymiles_hit_generation_control_function" not in export_allowed
    assert "number.hoymiles_hit_maximum_export_power_limit" not in export_allowed

    assert "number.hoymiles_hit_self_use_soc" not in scheduler
    tariff_snapshot = scheduler.split(
        "# Snapshot both shared registers, freeze the accepted action", 1
    )[1].split("# Idempotent setting writes", 1)[0]
    assert "ems_maximum_charge_power_readback" in tariff_snapshot
    assert "ems_force_charge_soc_readback" in tariff_snapshot
    rce_snapshot = scheduler.split(
        "# Snapshot both shared registers and claim durable RCE ownership", 1
    )[1].split("entity_id: input_boolean.hoymiles_rce_discharge_active", 1)[0]
    assert "ems_maximum_discharge_power_readback" in rce_snapshot
    assert "ems_force_discharge_soc_readback" in rce_snapshot
    balancing_snapshot = scheduler.split("hoymiles_start_battery_balancing:", 1)[1].split(
        "hoymiles_stop_battery_balancing:", 1
    )[0]
    assert "ems_maximum_charge_power_readback" in balancing_snapshot
    assert "ems_force_charge_soc_readback" in balancing_snapshot

    saved_gcf = scheduler.split("hoymiles_rcm_saved_export_limit:", 1)[1].split(
        "hoymiles_rcm_saved_max_discharge_power:", 1
    )[0]
    assert "min: -10" in saved_gcf and "max: 200" in saved_gcf
    gcf_helper = helpers.split("hoymiles_verified_set_gcf_export_limit:", 1)[1]
    assert "min: -10" in gcf_helper and "max: 200" in gcf_helper

    main = scheduler.split("id: hoymiles_rcm_voltage_charge_control", 1)[1].split(
        "id: hoymiles_rcm_pre_discharge_control", 1
    )[0]
    emergency = main.split(
        "# Charge and export ownership are independent", 1
    )[1].split("# Final emergency interlock", 1)[0]
    charge_call = emergency.index(
        "action: script.hoymiles_verified_set_battery_max_charge_power"
    )
    export_call = emergency.index(
        "action: script.hoymiles_verified_set_gcf_export_limit"
    )
    assert "continue_on_error: true" in emergency[charge_call:export_call]
    assert "continue_on_error: true" in emergency[export_call:]
    assert "battery_max_charge_power_readback" in emergency
    assert "gcf_maximum_export_power_readback" in emergency

    # A syntactically valid block scalar can still swallow an accidentally
    # indented ``then``. Parse the structure when PyYAML is available and prove
    # that balancing restores both owned 4304 and 4303 registers as actions.
    if yaml is not None:
        package = yaml.safe_load(scheduler)
        stop_sequence = package["script"]["hoymiles_stop_battery_balancing"][
            "sequence"
        ]
        restore_actions = {
            action.get("action")
            for item in stop_sequence
            if isinstance(item, dict) and "then" in item
            for action in item.get("then", [])
            if isinstance(action, dict)
        }
        assert "script.hoymiles_verified_set_ems_maximum_charge_power" in restore_actions
        assert "script.hoymiles_verified_set_ems_force_charge_soc" in restore_actions


def main() -> None:
    """Run all matrices without external test dependencies."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="run the full 2064-scenario pre-release matrix",
    )
    args = parser.parse_args()
    exhaustive = args.exhaustive
    rce_count, rce_statuses = run_rce_matrix(exhaustive=exhaustive)
    tariff_count, tariff_statuses = run_tariff_matrix(exhaustive=exhaustive)
    rcm_count, rcm_statuses = run_rcm_matrix(exhaustive=exhaustive)
    random_count = run_randomized_boundary_sweep(samples=120 if exhaustive else 40)
    assert_automation_interlocks()
    assert_manual_cycle_finalization_contracts()
    assert_tariff_startup_contracts()
    assert_tariff_execution_contracts()
    assert_rce_execution_contracts()
    assert_rcm_execution_contracts()
    assert_physical_hardware_readback_contracts()
    total = rce_count + tariff_count + rcm_count + random_count
    profile = "exhaustive" if exhaustive else "quick"
    print(f"Automation matrix ({profile}): {total} scenarios passed")
    print(f"  RCE: {rce_count} {dict(sorted(rce_statuses.items()))}")
    print(f"  Tariff: {tariff_count} {dict(sorted(tariff_statuses.items()))}")
    print(f"  RCEm: {rcm_count} {dict(sorted(rcm_statuses.items()))}")
    print(f"  Random RCE boundaries: {random_count}")
    print("  HA interlocks: RCE / tariff / RCEm / balancing / manual timers present")


if __name__ == "__main__":
    main()

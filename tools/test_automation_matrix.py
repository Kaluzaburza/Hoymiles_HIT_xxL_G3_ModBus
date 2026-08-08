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
    )
    for marker in required_markers:
        assert marker in source, f"Missing automation interlock marker: {marker}"


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

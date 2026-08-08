"""Replay the public RCEm controller against a voltage simulation CSV.

The file has no battery or command telemetry, so this validates scheduling,
bounds and recommendations only.  It deliberately does not claim that the
recommended charge power would produce a specific voltage reduction onsite.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "hoymiles_hit_modbus"))

from rcm_optimizer import RCMOptimizerInput, optimize_rcm  # noqa: E402


WARSAW = ZoneInfo("Europe/Warsaw")


def _number(row: dict[str, str], *names: str, default: float = 0.0) -> float:
    lowered = {key.casefold(): value for key, value in row.items()}
    for name in names:
        raw = lowered.get(name.casefold())
        if raw not in (None, ""):
            return float(raw.replace(",", "."))
    return default


def _timestamp(row: dict[str, str]) -> datetime:
    raw = next(
        value for key, value in row.items() if "time" in key.casefold() or "date" in key.casefold()
    )
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=WARSAW) if parsed.tzinfo is None else parsed.astimezone(WARSAW)


def main(path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    if not rows:
        raise RuntimeError("CSV has no samples")

    recommendations: list[float] = []
    risk_samples = 0
    emergency_samples = 0
    current_limit = 100.0
    current_export_limit = 100.0
    export_recommendations: list[float] = []
    for row in rows:
        now = _timestamp(row)
        l1 = _number(row, "voltage_l1_v", "grid_voltage_l1_v", "u_l1_v", "v_l1_v")
        l2 = _number(row, "voltage_l2_v", "grid_voltage_l2_v", "u_l2_v", "v_l2_v")
        l3 = _number(row, "voltage_l3_v", "grid_voltage_l3_v", "u_l3_v", "v_l3_v")
        pv_kw = _number(row, "pv_available_kw", "pv_power_kw")
        load_kw = _number(row, "load_kw", "load_power_kw", "house_load_kw")
        maximum = max(l1, l2, l3)
        result = optimize_rcm(
            RCMOptimizerInput(
                now=now,
                voltage_l1_v=l1,
                voltage_l2_v=l2,
                voltage_l3_v=l3,
                filtered_voltage_v=maximum,
                rolling_10m_voltage_v=maximum,
                historical_p90_voltage_v=maximum,
                risk_windows=((12 * 60 + 30, 14 * 60 + 15, 255.0),),
                history_days=4,
                pv_power_kw=pv_kw,
                load_power_kw=load_kw,
                grid_export_power_kw=max(pv_kw - load_kw, 0.0),
                battery_capacity_kwh=21.0,
                battery_soc_percent=50.0,
                reserve_soc_percent=25.0,
                safety_margin_soc_percent=2.0,
                protected_minimum_soc_percent=35.0,
                expected_risk_surplus_kwh=8.0,
                expected_natural_headroom_kwh=0.0,
                minutes_to_risk=max(12 * 60 + 30 - (now.hour * 60 + now.minute), 0),
                risk_day_offset=0,
                system_power_kw=10.0,
                battery_voltage_v=52.0,
                bms_max_charge_current_a=175.0,
                bms_max_discharge_current_a=175.0,
                current_charge_limit_percent=current_limit,
                saved_charge_limit_percent=100.0,
                export_control_enabled=True,
                current_export_limit_percent=current_export_limit,
                saved_export_limit_percent=100.0,
                user_export_cap_percent=100.0,
            )
        )
        current_limit = result.recommended_charge_limit_percent
        current_export_limit = result.recommended_export_limit_percent
        recommendations.append(current_limit)
        export_recommendations.append(current_export_limit)
        risk_samples += int(result.risk_window_active)
        emergency_samples += int(result.status_code == "emergency")

    assert all(10.0 <= value <= 100.0 for value in recommendations)
    assert all(0.0 <= value <= 100.0 for value in export_recommendations)
    print(f"samples={len(rows)}")
    print(f"risk_window_samples={risk_samples}")
    print(f"emergency_samples={emergency_samples}")
    print(f"recommended_limit_range={min(recommendations):.1f}..{max(recommendations):.1f}%")
    print(
        "recommended_export_range="
        f"{min(export_recommendations):.1f}..{max(export_recommendations):.1f}%"
    )
    print("note=command replay only; voltage response requires a real exporting site")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: simulate_rcm_csv.py PATH.csv")
    main(Path(sys.argv[1]))

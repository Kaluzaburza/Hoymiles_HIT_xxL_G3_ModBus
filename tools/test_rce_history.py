"""Deterministic tests for recorder-backed RCE LOAD history."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "custom_components"
    / "hoymiles_hit_modbus"
    / "rce_history.py"
)
SPEC = importlib.util.spec_from_file_location("hoymiles_rce_history", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load the RCE history helpers")
HISTORY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HISTORY
SPEC.loader.exec_module(HISTORY)

WARSAW = ZoneInfo("Europe/Warsaw")


def test_daily_and_night_history_uses_phase_counters() -> None:
    """Four complete days and nights must be restored across daily resets."""
    now = datetime(2026, 8, 1, 12, 0, tzinfo=WARSAW)
    samples = {entity_id: [] for entity_id in HISTORY.LOAD_PHASE_ENERGY_ENTITIES}
    totals = {
        date(2026, 7, 28): (10.0, 12.0, 2.0),
        date(2026, 7, 29): (11.0, 13.0, 3.0),
        date(2026, 7, 30): (12.0, 14.0, 4.0),
        date(2026, 7, 31): (13.0, 15.0, 5.0),
        date(2026, 8, 1): (3.0, 3.0, 1.0),
    }
    for day, phase_totals in totals.items():
        for entity_id, total in zip(
            HISTORY.LOAD_PHASE_ENERGY_ENTITIES,
            phase_totals,
            strict=True,
        ):
            samples[entity_id].extend(
                [
                    (
                        datetime.combine(day, time(0, 5), tzinfo=WARSAW),
                        0.0,
                    ),
                    (
                        datetime.combine(day, time(6, 25), tzinfo=WARSAW),
                        min(total, 1.0),
                    ),
                    (
                        datetime.combine(day, time(18, 55), tzinfo=WARSAW),
                        max(total - 2.0, 0.0),
                    ),
                    (
                        datetime.combine(day, time(23, 55), tzinfo=WARSAW),
                        total,
                    ),
                ]
            )

    windows = {
        day: (
            datetime.combine(day, time(19, 0), tzinfo=WARSAW),
            datetime.combine(
                day + timedelta(days=1),
                time(6, 30),
                tzinfo=WARSAW,
            ),
        )
        for day in (
            date(2026, 7, 28),
            date(2026, 7, 29),
            date(2026, 7, 30),
            date(2026, 7, 31),
        )
    }
    result = HISTORY.summarize_load_history(
        samples,
        now=now,
        night_windows=windows,
    )

    assert result.daily_history_days == 4
    assert result.daily_energy_kwh == {
        "2026-07-28": 24.0,
        "2026-07-29": 27.0,
        "2026-07-30": 30.0,
        "2026-07-31": 33.0,
    }
    assert abs(result.average_daily_kwh - 28.5) < 1e-6
    assert result.night_history_days == 4
    assert abs(result.average_night_kwh - 9.0) < 1e-6


if __name__ == "__main__":
    test_daily_and_night_history_uses_phase_counters()
    print("RCE history: recorder reconstruction scenario passed")

"""Deterministic tests for the four-day RCEm voltage history."""

from __future__ import annotations

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "custom_components" / "hoymiles_hit_modbus" / "rcm_history.py"
)
SPEC = importlib.util.spec_from_file_location("hoymiles_rcm_history", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load RCEm history helpers")
HISTORY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HISTORY
SPEC.loader.exec_module(HISTORY)

WARSAW = ZoneInfo("Europe/Warsaw")


def test_repeated_voltage_window_is_detected() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=WARSAW)
    samples = {entity_id: [] for entity_id in HISTORY.GRID_VOLTAGE_ENTITIES}
    for days_ago in range(1, 5):
        day = now - timedelta(days=days_ago)
        for hour in range(24):
            for minute in (0, 15, 30, 45):
                timestamp = day.replace(hour=hour, minute=minute, second=0)
                risky = (hour == 12 and minute >= 30) or hour == 13 or (
                    hour == 14 and minute == 0
                )
                base = 250.1 if risky else 237.0
                for phase, entity_id in enumerate(HISTORY.GRID_VOLTAGE_ENTITIES):
                    samples[entity_id].append((timestamp, base + phase * 0.2))

    result = HISTORY.summarize_voltage_history(samples, now=now)
    assert result.history_days == 4
    assert result.sample_count == 4 * 96 * 3
    assert result.risk_windows == ((750, 855, 250.48),)
    assert all(abs(value - 250.48) < 0.01 for value in result.profile_median_v[50:57])


def test_single_exceptional_day_does_not_schedule_control() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=WARSAW)
    samples = {entity_id: [] for entity_id in HISTORY.GRID_VOLTAGE_ENTITIES}
    for days_ago in range(1, 5):
        timestamp = (now - timedelta(days=days_ago)).replace(
            hour=13, minute=0, second=0
        )
        voltage = 257.0 if days_ago == 1 else 238.0
        for entity_id in HISTORY.GRID_VOLTAGE_ENTITIES:
            samples[entity_id].append((timestamp, voltage))

    result = HISTORY.summarize_voltage_history(samples, now=now)
    assert result.history_days == 4
    assert result.risk_windows == ()
    assert result.profile_median_v[52] == 238.0
    assert result.profile_p90_v[52] > 248.5


if __name__ == "__main__":
    test_repeated_voltage_window_is_detected()
    test_single_exceptional_day_does_not_schedule_control()
    print("RCEm history: repeated-window and outlier scenarios passed")

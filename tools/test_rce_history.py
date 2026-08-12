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
    assert len(result.average_profile_kwh) == 48
    assert abs(sum(result.average_profile_kwh) - 28.5) < 0.01
    assert result.weekday_profile_days == 4
    assert result.weekend_profile_days == 0
    assert len(result.weekday_profile_kwh) == 48


def test_current_day_window_uses_actual_phase_load() -> None:
    """Live daytime LOAD must be reconstructed from the phase counters."""
    now = datetime(2026, 8, 1, 12, 0, tzinfo=WARSAW)
    start = datetime(2026, 8, 1, 7, 30, tzinfo=WARSAW)
    samples = {entity_id: [] for entity_id in HISTORY.LOAD_PHASE_ENERGY_ENTITIES}
    for entity_id, before, current in zip(
        HISTORY.LOAD_PHASE_ENERGY_ENTITIES,
        (2.0, 3.0, 1.0),
        (5.0, 7.0, 2.5),
        strict=True,
    ):
        samples[entity_id] = [
            (start - timedelta(minutes=5), before),
            (now - timedelta(minutes=5), current),
        ]

    result = HISTORY.summarize_load_history(
        samples,
        now=now,
        night_windows={},
        current_day_window=(start, now),
    )

    assert abs(result.current_day_energy_kwh - 8.5) < 1e-6


def test_extended_history_exposes_28_complete_days_and_profiles() -> None:
    """The daily cache may retain 28 days without changing profile shape."""
    now = datetime(2026, 8, 31, 12, 0, tzinfo=WARSAW)
    samples = {entity_id: [] for entity_id in HISTORY.LOAD_PHASE_ENERGY_ENTITIES}
    for offset in range(28, 0, -1):
        day = now.date() - timedelta(days=offset)
        for phase, entity_id in enumerate(
            HISTORY.LOAD_PHASE_ENERGY_ENTITIES,
            start=1,
        ):
            samples[entity_id].extend(
                [
                    (datetime.combine(day, time(0, 5), tzinfo=WARSAW), 0.0),
                    (
                        datetime.combine(day, time(12, 5), tzinfo=WARSAW),
                        float(phase),
                    ),
                    (
                        datetime.combine(day, time(23, 55), tzinfo=WARSAW),
                        float(phase * 2),
                    ),
                ]
            )
    result = HISTORY.summarize_load_history(
        samples,
        now=now,
        night_windows={},
        history_days=28,
    )
    assert result.daily_history_days == 28
    assert len(result.daily_energy_kwh) == 28
    assert result.weekday_profile_days + result.weekend_profile_days == 28
    assert len(result.average_profile_kwh) == 48
    assert abs(sum(result.average_profile_kwh) - 12.0) < 0.01


if __name__ == "__main__":
    test_daily_and_night_history_uses_phase_counters()
    test_current_day_window_uses_actual_phase_load()
    test_extended_history_exposes_28_complete_days_and_profiles()
    print("RCE history: 3 recorder reconstruction scenarios passed")

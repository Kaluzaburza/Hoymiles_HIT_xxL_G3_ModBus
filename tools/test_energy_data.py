"""Deterministic tests for the shared energy-input freshness layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "hoymiles_hit_modbus"))

from energy_data import numeric_sample_is_fresh, numeric_state_sample  # noqa: E402


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


@dataclass
class FakeState:
    state: str
    last_updated: datetime
    last_reported: datetime | None = None


def main() -> None:
    zero = numeric_state_sample(
        FakeState("0", NOW, NOW),
        NOW,
        max_age_seconds=120,
        minimum=0,
    )
    assert zero.fresh and zero.value == 0.0 and zero.reason == "fresh"
    assert numeric_sample_is_fresh(0.0, 120.0, 120.0)
    assert not numeric_sample_is_fresh(None, 0.0, 120.0)
    assert not numeric_sample_is_fresh(float("nan"), 0.0, 120.0)
    assert not numeric_sample_is_fresh(1.0, 120.001, 120.0)
    assert numeric_sample_is_fresh(1.0, -5.0, 120.0)
    assert not numeric_sample_is_fresh(1.0, -5.001, 120.0)

    # A recent repeated report is authoritative even when last_updated is old.
    repeated = numeric_state_sample(
        FakeState("52", NOW - timedelta(hours=1), NOW - timedelta(seconds=10)),
        NOW,
        max_age_seconds=120,
        scale=0.001,
    )
    assert repeated.fresh and abs((repeated.value or 0.0) - 0.052) < 1e-9

    stale = numeric_state_sample(
        FakeState("10", NOW - timedelta(seconds=121)),
        NOW,
        max_age_seconds=120,
    )
    assert not stale.fresh and stale.value is None and stale.reason == "stale"

    future = numeric_state_sample(
        FakeState("10", NOW + timedelta(seconds=6)),
        NOW,
        max_age_seconds=120,
    )
    assert not future.fresh and future.reason == "future_timestamp"

    unavailable = numeric_state_sample(
        FakeState("unavailable", NOW),
        NOW,
        max_age_seconds=120,
    )
    assert not unavailable.fresh and unavailable.reason == "unavailable"

    negative = numeric_state_sample(
        FakeState("-1", NOW),
        NOW,
        max_age_seconds=120,
        minimum=0,
    )
    assert not negative.fresh and negative.reason == "below_minimum"

    # Control-domain sentinels must never become physically valid data.  The
    # engines apply these exact ranges to the physical FC03 mirrors.
    high_soc = numeric_state_sample(
        FakeState("101", NOW, NOW),
        NOW,
        max_age_seconds=120,
        minimum=0,
        maximum=100,
    )
    assert not high_soc.fresh and high_soc.reason == "above_maximum"
    zero_self_use = numeric_state_sample(
        FakeState("0", NOW, NOW),
        NOW,
        max_age_seconds=120,
        minimum=10,
        maximum=100,
    )
    assert not zero_self_use.fresh and zero_self_use.reason == "below_minimum"
    zero_charge_register = numeric_state_sample(
        FakeState("0", NOW, NOW),
        NOW,
        max_age_seconds=120,
        minimum=10,
        maximum=100,
    )
    assert (
        not zero_charge_register.fresh
        and zero_charge_register.reason == "below_minimum"
    )

    print("Shared energy data: freshness and zero-value contracts passed")


if __name__ == "__main__":
    main()

"""Shared, policy-neutral validation of Home Assistant energy inputs.

The three EMS policy engines intentionally make different decisions, but they
must agree on whether a numeric state is usable.  This module contains no RCE,
tariff or voltage-control policy; it only converts one HA-like state object
into an immutable value/age/freshness snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any


UNAVAILABLE_STATES = frozenset({"unknown", "unavailable", "none", ""})


@dataclass(frozen=True, slots=True)
class NumericStateSample:
    """One numeric state together with evidence that it is current."""

    value: float | None
    age_seconds: float | None
    fresh: bool
    reason: str
    reported_at: datetime | None


def state_reported_at(state: Any) -> datetime | None:
    """Prefer HA ``last_reported`` and safely fall back to ``last_updated``."""

    if state is None:
        return None
    reported = getattr(state, "last_reported", None) or getattr(
        state,
        "last_updated",
        None,
    )
    return reported if isinstance(reported, datetime) else None


def state_age_seconds(state: Any, now: datetime) -> float | None:
    """Return signed age on an absolute UTC timeline."""

    reported = state_reported_at(state)
    if reported is None:
        return None
    try:
        now_utc = now.astimezone(timezone.utc)
        reported_utc = reported.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None
    return (now_utc - reported_utc).total_seconds()


def numeric_sample_is_fresh(
    value: float | None,
    age_seconds: float | None,
    max_age_seconds: float,
    *,
    future_tolerance_seconds: float = 5.0,
) -> bool:
    """Return whether an already parsed numeric sample is safe to consume.

    ``0.0`` is valid data.  Keeping this rule in one policy-neutral helper
    prevents a contractual zero limit from becoming an unrestricted fallback
    in any of the three EMS engines.
    """

    return bool(
        value is not None
        and isfinite(value)
        and age_seconds is not None
        and isfinite(age_seconds)
        and -max(float(future_tolerance_seconds), 0.0)
        <= age_seconds
        <= max(float(max_age_seconds), 0.0)
    )


def numeric_state_sample(
    state: Any,
    now: datetime,
    *,
    max_age_seconds: float,
    scale: float = 1.0,
    minimum: float | None = None,
    maximum: float | None = None,
    future_tolerance_seconds: float = 5.0,
) -> NumericStateSample:
    """Validate one numeric state without choosing an engine fallback.

    Zero is always preserved.  Missing and stale values remain ``None`` so an
    individual policy can fail closed instead of accidentally converting a
    valid 0%/0 A limit into an unrestricted default.
    """

    if state is None:
        return NumericStateSample(None, None, False, "missing", None)

    reported = state_reported_at(state)
    age = state_age_seconds(state, now)
    raw = str(getattr(state, "state", "")).strip().casefold()
    if raw in UNAVAILABLE_STATES:
        return NumericStateSample(None, age, False, "unavailable", reported)
    try:
        value = float(raw) * float(scale)
    except (TypeError, ValueError):
        return NumericStateSample(None, age, False, "not_numeric", reported)
    if not isfinite(value):
        return NumericStateSample(None, age, False, "not_finite", reported)
    if age is None:
        return NumericStateSample(None, None, False, "missing_timestamp", reported)
    if age < -max(float(future_tolerance_seconds), 0.0):
        return NumericStateSample(None, age, False, "future_timestamp", reported)
    if age > max(float(max_age_seconds), 0.0):
        return NumericStateSample(None, age, False, "stale", reported)
    if minimum is not None and value < minimum:
        return NumericStateSample(None, age, False, "below_minimum", reported)
    if maximum is not None and value > maximum:
        return NumericStateSample(None, age, False, "above_maximum", reported)
    if not numeric_sample_is_fresh(
        value,
        age,
        max_age_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    ):
        return NumericStateSample(None, age, False, "stale", reported)
    return NumericStateSample(value, age, True, "fresh", reported)

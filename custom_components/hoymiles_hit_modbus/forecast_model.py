"""Shared conservative PV-forecast helpers.

The functions in this module deliberately have no Home Assistant imports so
the same forecast policy can be used by the RCE and tariff optimizers and can
be covered by deterministic tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
import re


LIVE_FORECAST_MIN_EXPECTED_KWH = 2.0
LIVE_FORECAST_FULL_CONFIDENCE_KWH = 6.0
LIVE_FORECAST_MIN_FACTOR = 0.15
ZERO_EXPORT_FORECAST_FACTOR = 0.80
_EPSILON = 1e-6
_PHYSICAL_EXPORT_HISTORY_MIN_VERSION = (1, 5, 2)
_PACKAGE_VERSION = re.compile(
    r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:[-+][0-9A-Za-z.-]{1,64})?$"
)


@dataclass(frozen=True, slots=True)
class ForecastLearningPolicy:
    """One deterministic decision about forecast-learning eligibility."""

    enabled: bool
    mode: str
    excluded_reason: str | None
    factor_override: float | None = None


def resolve_forecast_learning_policy(
    *,
    readback_verified: bool,
    gcf_enable_code: float | None,
    export_limit_percent: float | None,
    unverified_reason: str | None = None,
) -> ForecastLearningPolicy:
    """Resolve adaptive learning only from verified physical GCF readbacks.

    Register 258 decides whether register 259 is effective.  A disabled GCF
    leaves export unrestricted by this function.  An enabled GCF is treated
    as zero-export only for the exact, verified ``0.0`` readback; values
    outside the physical ``0..100`` percent range remain invalid evidence.
    """

    if not readback_verified:
        return ForecastLearningPolicy(
            enabled=False,
            mode="conservative_gcf_unverified",
            excluded_reason=unverified_reason or "gcf_readback_unverified",
        )
    if (
        gcf_enable_code is None
        or not isfinite(gcf_enable_code)
        or gcf_enable_code not in {0.0, 1.0}
    ):
        return ForecastLearningPolicy(
            enabled=False,
            mode="conservative_gcf_unverified",
            excluded_reason="gcf_enable_invalid",
        )
    if gcf_enable_code == 0.0:
        return ForecastLearningPolicy(True, "adaptive", None)
    if (
        export_limit_percent is None
        or not isfinite(export_limit_percent)
        or export_limit_percent < 0.0
        or export_limit_percent > 100.0
    ):
        return ForecastLearningPolicy(
            enabled=False,
            mode="conservative_gcf_unverified",
            excluded_reason="gcf_limit_invalid",
        )

    if export_limit_percent == 0.0:
        return ForecastLearningPolicy(
            enabled=False,
            mode="fixed_zero_export",
            excluded_reason="zero_export",
            factor_override=ZERO_EXPORT_FORECAST_FACTOR,
        )
    return ForecastLearningPolicy(True, "adaptive", None)


def forecast_factor_for_policy(
    policy: ForecastLearningPolicy,
    historical_factor: float,
) -> float:
    """Return the factor consumed by planners without mutating their model."""

    historical = min(max(historical_factor, LIVE_FORECAST_MIN_FACTOR), 1.0)
    if policy.factor_override is not None:
        return policy.factor_override
    if not policy.enabled:
        return min(historical, ZERO_EXPORT_FORECAST_FACTOR)
    return historical


def forecast_learning_history_day_eligible(
    export_allowed_history: Sequence[tuple[datetime, str]],
    package_version_history: Sequence[tuple[datetime, str]],
    *,
    day_start: datetime,
    day_end: datetime,
) -> bool:
    """Return whether verified export remained allowed for a whole day.

    Since managed package v1.5.2,
    ``binary_sensor.hoymiles_ems_export_allowed`` is derived from the physical
    258/259 readbacks and their completed FC03 generation.  Earlier package
    versions used UI values under the same stable entity ID, so a second
    version-history boundary deliberately excludes those legacy days.  Any
    zero-export, stale, unavailable, missing or pre-v1.5.2 interval rejects the
    complete cumulative-PV day so it can never re-enter adaptive learning.
    """

    if day_end <= day_start:
        return False

    def _window_states(
        history: Sequence[tuple[datetime, str]],
    ) -> tuple[str | None, list[str]] | None:
        state_at_start: str | None = None
        intraday: list[str] = []
        try:
            ordered = sorted(history, key=lambda item: item[0])
            for reported_at, raw_state in ordered:
                state = str(raw_state).strip().casefold()
                if reported_at <= day_start:
                    state_at_start = state
                elif reported_at < day_end:
                    intraday.append(state)
                else:
                    break
        except (AttributeError, TypeError, ValueError):
            return None
        return state_at_start, intraday

    export_states = _window_states(export_allowed_history)
    version_states = _window_states(package_version_history)
    if export_states is None or version_states is None:
        return False
    export_at_start, export_intraday = export_states
    if export_at_start != "on" or any(
        state != "on" for state in export_intraday
    ):
        return False

    def _physical_history_version(raw: str) -> bool:
        match = _PACKAGE_VERSION.fullmatch(raw)
        if match is None:
            return False
        try:
            version = tuple(int(part) for part in match.groups()[:3])
        except (TypeError, ValueError):
            return False
        return version >= _PHYSICAL_EXPORT_HISTORY_MIN_VERSION

    version_at_start, version_intraday = version_states
    if version_at_start is None or not _physical_history_version(
        version_at_start
    ):
        return False
    return all(_physical_history_version(state) for state in version_intraday)


def adaptive_forecast_factor(
    historical_factor: float,
    actual_energy_kwh: float,
    expected_elapsed_kwh: float | None,
    *,
    eligible: bool,
) -> tuple[float, float | None, float]:
    """Blend complete-day history with live cumulative PV underproduction.

    The live signal is ignored while the sample is too small and can only
    lower today's forecast.  This prevents a short sunny interval from making
    the home reserve less conservative, while a failed PV string is detected
    during the same day.
    """

    historical = min(max(historical_factor, LIVE_FORECAST_MIN_FACTOR), 1.0)
    if (
        not eligible
        or expected_elapsed_kwh is None
        or expected_elapsed_kwh < LIVE_FORECAST_MIN_EXPECTED_KWH
    ):
        return historical, None, 0.0

    live_ratio = min(
        max(actual_energy_kwh / max(expected_elapsed_kwh, _EPSILON), 0.0),
        1.10,
    )
    confidence = min(
        max(
            expected_elapsed_kwh / LIVE_FORECAST_FULL_CONFIDENCE_KWH,
            0.0,
        ),
        1.0,
    )
    conservative_live = min(
        max(live_ratio, LIVE_FORECAST_MIN_FACTOR),
        historical,
    )
    effective = historical + (conservative_live - historical) * confidence
    return (
        min(max(effective, LIVE_FORECAST_MIN_FACTOR), historical),
        live_ratio,
        confidence,
    )


def robust_weighted_factor(
    samples: Sequence[tuple[float, float]],
) -> tuple[float, float, int]:
    """Return a recency-weighted robust factor and uncertainty estimate.

    Each sample is ``(age_days, actual/forecast ratio)``.  A seven-day
    half-life lets the model adapt to seasonal or installation changes without
    allowing one cloudy or incomplete day to dominate.  Weighted medians and
    MAD are used instead of a mean so outliers remain bounded.
    """

    values = [
        (
            min(max(float(age_days), 0.0), 365.0),
            min(max(float(ratio), LIVE_FORECAST_MIN_FACTOR), 1.10),
        )
        for age_days, ratio in samples
    ]
    if not values:
        return 0.90, 0.15, 0

    weighted = [
        (ratio, 0.5 ** (age_days / 7.0))
        for age_days, ratio in values
    ]

    def weighted_median(items: Sequence[tuple[float, float]]) -> float:
        ordered = sorted(items, key=lambda item: item[0])
        threshold = sum(weight for _, weight in ordered) / 2.0
        cumulative = 0.0
        for value, weight in ordered:
            cumulative += weight
            if cumulative >= threshold:
                return value
        return ordered[-1][0]

    center = weighted_median(weighted)
    deviations = [
        (abs(value - center), weight) for value, weight in weighted
    ]
    uncertainty = min(weighted_median(deviations) * 1.4826, 0.50)
    # Automatic correction is deliberately never optimistic.
    return min(center, 1.0), uncertainty, len(values)


def uncertainty_risk_weight(
    *,
    history_days: int,
    live_confidence: float,
    uncertainty_available: bool,
) -> float:
    """Return how strongly reserve calculations should follow Solcast P10.

    With no uncertainty band we cannot create a synthetic P10 value and return
    zero.  Otherwise sparse history uses most of the low forecast, while four
    or more complete days plus a strong live sample allow a moderate blend
    toward P50.  The reserve is therefore conservative without permanently
    discarding all forecast upside.
    """

    if not uncertainty_available:
        return 0.0
    history_confidence = min(max(history_days / 4.0, 0.0), 1.0)
    confidence = 0.75 * history_confidence + 0.25 * min(
        max(live_confidence, 0.0),
        1.0,
    )
    return min(max(0.80 - 0.35 * confidence, 0.45), 0.80)


def blend_low_expected(
    low: float,
    expected: float,
    risk_weight: float,
) -> float:
    """Blend a low (P10) and expected (P50) energy estimate safely."""

    expected_value = max(expected, 0.0)
    low_value = min(max(low, 0.0), expected_value)
    weight = min(max(risk_weight, 0.0), 1.0)
    return expected_value * (1.0 - weight) + low_value * weight

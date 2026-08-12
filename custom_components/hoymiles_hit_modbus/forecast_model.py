"""Shared conservative PV-forecast helpers.

The functions in this module deliberately have no Home Assistant imports so
the same forecast policy can be used by the RCE and tariff optimizers and can
be covered by deterministic tests.
"""

from __future__ import annotations

from collections.abc import Sequence


LIVE_FORECAST_MIN_EXPECTED_KWH = 2.0
LIVE_FORECAST_FULL_CONFIDENCE_KWH = 6.0
LIVE_FORECAST_MIN_FACTOR = 0.15
_EPSILON = 1e-6


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

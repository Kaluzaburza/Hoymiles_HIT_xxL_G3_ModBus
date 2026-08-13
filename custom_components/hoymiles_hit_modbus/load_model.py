"""Policy-neutral robust estimates for household energy demand.

The EMS engines have different objectives, but they must derive the same
P50-like estimate and upper-load envelope from an identical recorder sample.
This pure module owns only sample validation, recency weighting and robust
quantiles. It deliberately contains no tariff, RCE or voltage-control rules.
"""

from __future__ import annotations

from collections.abc import Sequence


_EPSILON = 1e-6


def _clean_and_winsorise(
    values: Sequence[float],
    *,
    max_points: int,
) -> tuple[list[float], list[float]]:
    """Return recent valid values and a common robust clipping envelope."""

    clean = [float(value) for value in values if 0.0 < float(value) < 1_000.0]
    clean = clean[-max(max_points, 1) :]
    if not clean:
        return [], []
    ordered = sorted(clean)
    middle = len(ordered) // 2
    centre = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    lower = max(centre * 0.45, 0.05)
    upper = max(centre * 1.75, lower)
    return clean, [min(max(value, lower), upper) for value in clean]


def robust_weighted_estimate(
    values: Sequence[float],
    *,
    max_points: int = 28,
) -> tuple[float | None, float, int]:
    """Return recency-weighted demand, relative uncertainty and sample count."""

    clean, clipped = _clean_and_winsorise(values, max_points=max_points)
    if not clean:
        return None, 0.0, 0
    weights = [
        0.5 ** ((len(clipped) - 1 - index) / 7.0)
        for index in range(len(clipped))
    ]
    weight_sum = sum(weights)
    estimate = sum(
        value * weight for value, weight in zip(clipped, weights)
    ) / weight_sum
    uncertainty = sum(
        abs(value - estimate) / max(estimate, _EPSILON) * weight
        for value, weight in zip(clipped, weights)
    ) / weight_sum
    return estimate, min(max(uncertainty, 0.0), 1.0), len(clean)


def robust_weighted_upper_estimate(
    values: Sequence[float],
    *,
    max_points: int = 28,
    quantile: float = 0.90,
) -> tuple[float | None, int]:
    """Return a recency-weighted, winsorised upper demand quantile."""

    clean, clipped = _clean_and_winsorise(values, max_points=max_points)
    if not clean:
        return None, 0
    weighted = sorted(
        (
            value,
            0.5 ** ((len(clipped) - 1 - index) / 7.0),
        )
        for index, value in enumerate(clipped)
    )
    threshold = sum(weight for _, weight in weighted) * min(
        max(quantile, 0.0),
        1.0,
    )
    cumulative = 0.0
    for value, weight in weighted:
        cumulative += weight
        if cumulative + _EPSILON >= threshold:
            return value, len(clean)
    return weighted[-1][0], len(clean)

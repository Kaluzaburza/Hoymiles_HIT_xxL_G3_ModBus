"""Revision guard for executor-backed optimizer publications.

The Home Assistant event loop may update an optimizer input while its pure
solver is running in an executor.  A lock keeps solver calls single-flight,
but cannot make the input snapshot atomic across that await.  This module
provides the small, dependency-free revision primitive used to reject such a
stale result before it reaches an entity state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


MAX_IMMEDIATE_RECALCULATIONS = 3
INPUT_RECALCULATION_DELAY_SECONDS = 1.0

# These are the only cross-optimizer attributes consumed as planning inputs.
# Publication diagnostics such as ``result_current`` are deliberately absent:
# otherwise RCE and tariff would invalidate one another merely by announcing
# that their own recalculation is pending.
RCE_LOAD_BROKER_ATTRIBUTES = frozenset(
    {
        "load_profile_generated_at",
        "selected_average_daily_load_kwh",
        "average_night_load_4d_kwh",
        "recorder_load_daily_kwh",
        "provisional_daily_load_projection_kwh",
        "recorder_load_profile_30m_kwh",
        "recorder_load_average_profile_30m_kwh",
        "recorder_load_weekday_profile_30m_kwh",
        "recorder_load_weekend_profile_30m_kwh",
        "history_complete",
    }
)
TARIFF_PRICE_BROKER_ATTRIBUTES = frozenset(
    {
        "tariff_profile_g11_price",
        "g11_reference_price_pln_kwh",
        "current_price_pln_kwh",
    }
)


def _freeze(value: Any) -> Any:
    """Return a stable, equality-comparable representation of HA data."""
    if isinstance(value, Mapping):
        return tuple(
            sorted((str(key), _freeze(item)) for key, item in value.items())
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def state_fingerprint(
    state: Any,
    *,
    attributes: Iterable[str] | None = None,
    include_state: bool = True,
    include_last_updated: bool = True,
) -> tuple[Any, ...] | None:
    """Fingerprint exactly the state fields consumed by an optimizer.

    Counterpart optimizer entities deliberately pass an attribute allow-list
    and disable ``include_last_updated``.  Their publication-validity flags
    and diagnostic timestamps are not inputs, so excluding them prevents the
    RCE and tariff optimizers from invalidating each other indefinitely.
    """
    if state is None:
        return None
    raw_attributes = getattr(state, "attributes", {})
    if attributes is None:
        selected_attributes: Any = raw_attributes
    else:
        selected_attributes = {
            key: raw_attributes.get(key)
            for key in attributes
            if key in raw_attributes
        }
    # Freshness-sensitive inputs consume ``last_reported`` (HA 2024.3+) even
    # when their numeric value is unchanged.  Keep the parameter name for the
    # small public helper contract, but fingerprint the same timestamp source
    # as ``numeric_state_sample``.
    updated = (
        (
            getattr(state, "last_reported", None)
            or getattr(state, "last_updated", None)
        )
        if include_last_updated
        else None
    )
    return (
        getattr(state, "state", None) if include_state else None,
        _freeze(selected_attributes),
        _freeze(updated),
    )


def optimizer_input_fingerprint(
    hass: Any,
    entity_ids: Iterable[str],
    *,
    attribute_projections: Mapping[str, Iterable[str]] | None = None,
) -> tuple[tuple[str, tuple[Any, ...] | None], ...]:
    """Snapshot watched HA inputs without trusting listener scheduling order."""
    projections = attribute_projections or {}
    return tuple(
        (
            entity_id,
            state_fingerprint(
                hass.states.get(entity_id),
                attributes=projections.get(entity_id),
                include_state=entity_id not in projections,
                # Counterpart optimizer publications are projected to the
                # exact broker attributes above.  Their report timestamps are
                # diagnostics, while physical source timestamps are consumed
                # by signed-age freshness gates and must invalidate a result.
                include_last_updated=entity_id not in projections,
            ),
        )
        for entity_id in sorted(entity_ids)
    )


@dataclass(slots=True)
class OptimizerInputRevision:
    """Monotonic revision for one optimizer's consumed inputs."""

    _value: int = 0

    @property
    def value(self) -> int:
        """Return the current monotonic revision."""
        return self._value

    def invalidate(self) -> int:
        """Record a known input mutation and return the new revision."""
        self._value += 1
        return self._value

    def invalidate_state_change(
        self,
        old_state: Any,
        new_state: Any,
        *,
        attributes: Iterable[str] | None = None,
        include_state: bool = True,
        include_last_updated: bool = True,
    ) -> bool:
        """Advance only when a consumed state projection actually changed."""
        old_fingerprint = state_fingerprint(
            old_state,
            attributes=attributes,
            include_state=include_state,
            include_last_updated=include_last_updated,
        )
        new_fingerprint = state_fingerprint(
            new_state,
            attributes=attributes,
            include_state=include_state,
            include_last_updated=include_last_updated,
        )
        if old_fingerprint == new_fingerprint:
            return False
        self.invalidate()
        return True

    def is_current(self, captured_revision: int) -> bool:
        """Return whether an awaited calculation still matches its inputs."""
        return captured_revision == self._value

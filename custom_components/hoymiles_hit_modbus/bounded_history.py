"""Bounded Recorder reads used by background optimizer warmups."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from sqlalchemy import select

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.db_schema import States
from homeassistant.components.recorder.util import session_scope
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


# The shipped firmware reports fast voltages every 13 s (about 33k reports in
# five days) and standard energy counters every 150 s (about 18k in 31 days).
# This budget preserves those intended horizons while rejecting pathological
# Recorder growth before a query can materialize an unbounded result.
RECORDER_STATES_PER_ENTITY_LIMIT = 50_000
RECORDER_QUERY_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class RecorderStateSample:
    """Minimal Recorder row consumed by the optimizer history builders."""

    state: str
    last_updated: datetime


class RecorderHistoryLimitExceeded(RuntimeError):
    """Raised when a Recorder result reaches the explicit safety budget."""


class RecorderHistoryQueryTimeout(RuntimeError):
    """Raised when a bounded Recorder query does not finish in time."""


def _query_state_reports(
    hass: HomeAssistant,
    start_time: datetime,
    end_time: datetime,
    entity_id: str,
    limit: int,
) -> tuple[list[RecorderStateSample], bool]:
    """Read all stored reports for one entity with an SQL-level limit."""
    start_timestamp = start_time.timestamp()
    end_timestamp = end_time.timestamp()
    with session_scope(hass=hass, read_only=True) as session:
        recorder = get_recorder_instance(hass)
        metadata_id = recorder.states_meta_manager.get(
            entity_id,
            session,
            False,
        )
        if metadata_id is None:
            return [], False

        columns = (States.state, States.last_updated_ts)
        previous = session.execute(
            select(*columns)
            .where(
                States.metadata_id == metadata_id,
                States.last_updated_ts <= start_timestamp,
            )
            .order_by(States.last_updated_ts.desc())
            .limit(1)
        ).first()
        rows = list(
            session.execute(
                select(*columns)
                .where(
                    States.metadata_id == metadata_id,
                    States.last_updated_ts > start_timestamp,
                    States.last_updated_ts < end_timestamp,
                )
                .order_by(States.last_updated_ts)
                .limit(limit + 1)
            )
        )
        exceeded = len(rows) > limit
        if exceeded:
            return [], True
        if previous is not None:
            rows.insert(0, previous)
        return [
            RecorderStateSample(
                state=row.state,
                last_updated=dt_util.utc_from_timestamp(row.last_updated_ts),
            )
            for row in rows
            if row.state is not None and row.last_updated_ts is not None
        ], False


async def async_get_bounded_state_reports(
    hass: HomeAssistant,
    start_time: datetime,
    end_time: datetime,
    entity_ids: Sequence[str],
    *,
    limit_per_entity: int = RECORDER_STATES_PER_ENTITY_LIMIT,
    timeout_seconds: float = RECORDER_QUERY_TIMEOUT_SECONDS,
) -> dict[str, list[RecorderStateSample]]:
    """Return all stored reports with explicit time and row budgets.

    Repeated reports are intentionally preserved.  RCE uses them to prove
    counter coverage at time-window boundaries, while RCEm uses their presence
    in each 15-minute slot.  Asking SQL for one extra row makes a truncated
    result distinguishable from complete history; incomplete history is
    rejected rather than silently used by a controller.

    Queries are deliberately sequential.  A timeout cannot stop a database
    worker that is already executing, but at most one bounded query may then
    outlive the awaiting warmup.
    """
    if end_time <= start_time:
        raise ValueError("end_time must be later than start_time")
    if limit_per_entity < 1:
        raise ValueError("limit_per_entity must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    recorder = get_recorder_instance(hass)
    result: dict[str, list[RecorderStateSample]] = {}
    for entity_id in entity_ids:
        query = partial(
            _query_state_reports,
            hass,
            start_time,
            end_time,
            entity_id,
            limit_per_entity,
        )
        try:
            states, exceeded = await asyncio.wait_for(
                recorder.async_add_executor_job(query),
                timeout=timeout_seconds,
            )
        except TimeoutError as err:
            raise RecorderHistoryQueryTimeout(
                f"Recorder history query timed out for {entity_id}"
            ) from err
        if exceeded:
            raise RecorderHistoryLimitExceeded(
                f"Recorder history exceeded {limit_per_entity} reports for "
                f"{entity_id}"
            )
        result[entity_id] = states
    return result

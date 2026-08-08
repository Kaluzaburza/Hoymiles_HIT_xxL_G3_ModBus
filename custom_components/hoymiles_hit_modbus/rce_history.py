"""Recorder-backed LOAD history helpers for the RCE optimizer.

The aggregate LOAD register includes inverter self-consumption on parallel
systems.  These helpers rebuild complete-day and protected-night history from
the three per-phase daily energy counters, which represent the actual loads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta


LOAD_PHASE_ENERGY_ENTITIES = (
    "sensor.hoymiles_hit_load_energy_use_l1n_today",
    "sensor.hoymiles_hit_load_energy_use_l2n_today",
    "sensor.hoymiles_hit_load_energy_use_l3n_today",
)


@dataclass(frozen=True, slots=True)
class LoadHistorySummary:
    """Four-day LOAD history reconstructed from phase energy counters."""

    average_daily_kwh: float | None
    daily_history_days: int
    daily_energy_kwh: dict[str, float]
    average_night_kwh: float | None
    night_history_days: int
    night_energy_kwh: dict[str, float]
    current_day_energy_kwh: float | None = None
    average_profile_kwh: tuple[float, ...] = ()
    weekday_profile_kwh: tuple[float, ...] = ()
    weekend_profile_kwh: tuple[float, ...] = ()
    weekday_profile_days: int = 0
    weekend_profile_days: int = 0

    @property
    def daily_energy_total_kwh(self) -> float:
        """Return energy represented by complete daily history."""
        return sum(self.daily_energy_kwh.values())

    @property
    def night_energy_total_kwh(self) -> float:
        """Return energy represented by complete night history."""
        return sum(self.night_energy_kwh.values())


def _counter_increase(
    samples: Sequence[tuple[datetime, float]],
    start: datetime,
    end: datetime,
) -> float | None:
    """Return a daily-reset counter increase inside an interval."""
    before = [sample for sample in samples if sample[0] <= start]
    inside = [sample for sample in samples if start < sample[0] <= end]
    if not before or not inside:
        return None

    start_sample = before[-1]
    end_sample = inside[-1]
    # A large gap at either edge means recorder coverage is not reliable.
    if start - start_sample[0] > timedelta(hours=2):
        return None
    if end - end_sample[0] > timedelta(hours=2):
        return None

    previous_time, previous_value = start_sample
    energy = 0.0
    for sample_time, value in inside:
        if value >= previous_value:
            energy += value - previous_value
        elif sample_time.date() != previous_time.date():
            # Expected midnight reset of the "Today" counter.
            energy += value
        # Ignore same-day negative glitches instead of treating them as resets.
        previous_time = sample_time
        previous_value = value
    return max(energy, 0.0)


def summarize_load_history(
    samples_by_entity: Mapping[str, Sequence[tuple[datetime, float]]],
    *,
    now: datetime,
    night_windows: Mapping[date, tuple[datetime, datetime]],
    current_day_window: tuple[datetime, datetime] | None = None,
    history_days: int = 4,
) -> LoadHistorySummary:
    """Build complete-day and protected-night LOAD averages.

    Daily values use the maximum of each phase's monotonic daily counter.  A
    day is accepted only if all phases have a late-evening recorder sample.
    Night values integrate the same counters across their midnight reset.
    """
    normalized = {
        entity_id: sorted(samples, key=lambda item: item[0])
        for entity_id, samples in samples_by_entity.items()
    }

    daily: dict[str, float] = {}
    daily_profiles: dict[date, tuple[float, ...]] = {}
    candidate_days = [
        now.date() - timedelta(days=offset)
        for offset in range(history_days, 0, -1)
    ]
    for candidate in candidate_days:
        day_end = datetime.combine(
            candidate + timedelta(days=1),
            datetime.min.time(),
            tzinfo=now.tzinfo,
        )
        phase_totals: list[float] = []
        complete = True
        for entity_id in LOAD_PHASE_ENERGY_ENTITIES:
            samples = [
                item
                for item in normalized.get(entity_id, ())
                if item[0].date() == candidate
            ]
            if not samples or day_end - samples[-1][0] > timedelta(hours=2):
                complete = False
                break
            phase_totals.append(max(value for _, value in samples))
        if complete:
            daily[candidate.isoformat()] = round(sum(phase_totals), 3)
            slots = [0.0] * 48
            for entity_id in LOAD_PHASE_ENERGY_ENTITIES:
                previous = 0.0
                for sample_time, value in (
                    item
                    for item in normalized.get(entity_id, ())
                    if item[0].date() == candidate
                ):
                    if value + 1e-6 < previous:
                        # Ignore same-day negative recorder glitches.
                        previous = value
                        continue
                    increase = max(value - previous, 0.0)
                    slot = min(sample_time.hour * 2 + sample_time.minute // 30, 47)
                    slots[slot] += increase
                    previous = value
            profile_total = sum(slots)
            daily_total = sum(phase_totals)
            if profile_total > 0 and daily_total > 0:
                scale = daily_total / profile_total
                daily_profiles[candidate] = tuple(
                    max(value * scale, 0.0) for value in slots
                )

    nights: dict[str, float] = {}
    for night_date, (start, end) in sorted(night_windows.items()):
        if end > now:
            continue
        phase_energy: list[float] = []
        for entity_id in LOAD_PHASE_ENERGY_ENTITIES:
            increase = _counter_increase(
                normalized.get(entity_id, ()),
                start,
                end,
            )
            if increase is None:
                phase_energy = []
                break
            phase_energy.append(increase)
        if phase_energy:
            nights[night_date.isoformat()] = round(sum(phase_energy), 3)
    if len(nights) > history_days:
        nights = dict(list(nights.items())[-history_days:])

    current_day_energy: float | None = None
    if current_day_window is not None:
        current_start, current_end = current_day_window
        if current_end > current_start:
            phase_energy: list[float] = []
            for entity_id in LOAD_PHASE_ENERGY_ENTITIES:
                increase = _counter_increase(
                    normalized.get(entity_id, ()),
                    current_start,
                    current_end,
                )
                if increase is None:
                    phase_energy = []
                    break
                phase_energy.append(increase)
            if phase_energy:
                current_day_energy = round(sum(phase_energy), 3)

    def average_profile(days: Sequence[date]) -> tuple[float, ...]:
        profiles = [daily_profiles[day] for day in days if day in daily_profiles]
        if not profiles:
            return ()
        return tuple(
            round(sum(profile[index] for profile in profiles) / len(profiles), 4)
            for index in range(48)
        )

    profile_days = sorted(daily_profiles)
    weekday_days = [day for day in profile_days if day.weekday() < 5]
    weekend_days = [day for day in profile_days if day.weekday() >= 5]

    return LoadHistorySummary(
        average_daily_kwh=(
            round(sum(daily.values()) / len(daily), 3) if daily else None
        ),
        daily_history_days=len(daily),
        daily_energy_kwh=daily,
        average_night_kwh=(
            round(sum(nights.values()) / len(nights), 3) if nights else None
        ),
        night_history_days=len(nights),
        night_energy_kwh=nights,
        current_day_energy_kwh=current_day_energy,
        average_profile_kwh=average_profile(profile_days),
        weekday_profile_kwh=average_profile(weekday_days),
        weekend_profile_kwh=average_profile(weekend_days),
        weekday_profile_days=len(weekday_days),
        weekend_profile_days=len(weekend_days),
    )

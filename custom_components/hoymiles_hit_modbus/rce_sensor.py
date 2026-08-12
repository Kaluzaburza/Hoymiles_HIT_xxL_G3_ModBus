"""Home Assistant sensor exposing the optimized two-day RCE plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from functools import partial
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .forecast_model import (
    adaptive_forecast_factor,
    blend_low_expected,
    robust_weighted_factor,
    uncertainty_risk_weight,
)
from .models import RuntimeData
from .rce_history import (
    LOAD_PHASE_ENERGY_ENTITIES,
    LoadHistorySummary,
    summarize_load_history,
)
from .rce_optimizer import (
    OptimizerInput,
    OptimizerResult,
    floor_half_hour,
    optimize_rce,
    parse_rce_rows,
)


_LOGGER = logging.getLogger(__name__)
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_MIN_COMPLETE_RCE_DAY_PERIODS = 92

TODAY_FORECAST_CANDIDATES = (
    "sensor.solcast_pv_forecast_forecast_today",
    "sensor.solcast_pv_forecast_prognoza_na_dzisiaj",
    "sensor.solcast_forecast_today",
)
TOMORROW_FORECAST_CANDIDATES = (
    "sensor.solcast_pv_forecast_forecast_tomorrow",
    "sensor.solcast_pv_forecast_prognoza_na_jutro",
    "sensor.solcast_forecast_tomorrow",
)
DAY3_FORECAST_CANDIDATES = (
    "sensor.solcast_pv_forecast_forecast_day_3",
    "sensor.solcast_pv_forecast_day_3",
    "sensor.solcast_pv_forecast_prognoza_na_dzien_3",
    "sensor.solcast_forecast_day_3",
)
REMAINING_TODAY_CANDIDATES = (
    "sensor.solcast_pv_forecast_forecast_remaining_today",
    "sensor.solcast_pv_forecast_pozostala_prognoza_na_dzis",
    "sensor.solcast_forecast_remaining_today",
)

WATCHED_ENTITIES = {
    "sensor.hoymiles_rce_day",
    "sensor.hoymiles_rce_day_tomorrow",
    "sensor.hoymiles_hit_battery_capacity",
    "sensor.hoymiles_hit_overview_battery_soc",
    "sensor.hoymiles_hit_number_of_machines_master_and_slave",
    "sensor.hoymiles_hit_pv_total_energy_today",
    "sensor.hoymiles_hit_pv_to_load_energy_today",
    "sensor.hoymiles_hit_energy_from_battery_today",
    "sensor.hoymiles_hit_energy_from_grid_today",
    "sensor.hoymiles_hit_load_from_pv_power",
    "sensor.hoymiles_hit_overview_pv_total_power",
    "sensor.hoymiles_actual_load_power",
    "sensor.hoymiles_actual_load_energy_today",
    *LOAD_PHASE_ENERGY_ENTITIES,
    "sensor.hoymiles_hit_load_power_l1n",
    "sensor.hoymiles_hit_load_power_l2n",
    "sensor.hoymiles_hit_load_power_l3n",
    "sensor.hoymiles_hit_overview_load_active_power",
    "sensor.hoymiles_load_average_4_days",
    "sensor.hoymiles_night_load_average_4_days",
    "sensor.hoymiles_hit_battery_voltage_bms",
    "sensor.hoymiles_hit_maximum_discharge_current",
    "number.hoymiles_hit_self_use_soc",
    "number.hoymiles_hit_force_discharge_soc",
    "number.hoymiles_hit_maximum_discharge_power",
    "number.hoymiles_hit_maximum_export_power_limit",
    "select.hoymiles_hit_generation_control_function",
    "sensor.hoymiles_rce_effective_export_power",
    "sensor.hoymiles_rce_learned_export_power",
    "sensor.hoymiles_hit_tariff_charge_plan",
    "input_boolean.hoymiles_rce_discharge_enabled",
    "input_boolean.hoymiles_rce_dynamic_soc_enabled",
    "input_boolean.hoymiles_sale_block_enabled",
    "input_datetime.hoymiles_sale_block_start",
    "input_datetime.hoymiles_sale_block_end",
    "input_number.hoymiles_rce_soc_safety_margin",
    "input_number.hoymiles_rce_export_efficiency",
    "input_number.hoymiles_rce_fallback_daily_load",
    "input_number.hoymiles_rce_requested_discharge_power",
    "input_number.hoymiles_rce_battery_wear_cost",
    "input_number.hoymiles_tariff_g11_price",
    "input_number.hoymiles_tariff_charge_efficiency",
    "input_number.hoymiles_tariff_discharge_efficiency",
    "input_select.hoymiles_rce_inverter_rated_power",
    "input_text.hoymiles_solcast_forecast_today_entity",
    "input_text.hoymiles_solcast_forecast_tomorrow_entity",
    "sun.sun",
    *TODAY_FORECAST_CANDIDATES,
    *TOMORROW_FORECAST_CANDIDATES,
    *DAY3_FORECAST_CANDIDATES,
    *REMAINING_TODAY_CANDIDATES,
}

STATUS_TEXT = {
    "pl": {
        "ready": "Gotowa — plan zoptymalizowany",
        "waiting_for_market": "Oczekiwanie — brak dostępnego okna rynkowego",
        "home_protected": "Zasilanie domu zabezpieczone — brak energii na sprzedaż",
        "home_energy_shortage": "Za mało energii na potrzeby domu — sprzedaż zablokowana",
        "missing_data": "Brak wymaganych danych — sprzedaż zablokowana",
        "optimizer_error": "Błąd obliczeń — sprzedaż zablokowana",
        "zero_export": "Eksport zablokowany — aktywny limit GCF 0%",
    },
    "en": {
        "ready": "Ready — optimized plan",
        "waiting_for_market": "Waiting — no available market window",
        "home_protected": "Home supply protected — no energy available for export",
        "home_energy_shortage": "Insufficient home energy — export blocked",
        "missing_data": "Required data missing — export blocked",
        "optimizer_error": "Calculation error — export blocked",
        "zero_export": "Export blocked — active GCF limit is 0%",
    },
}

TODAY_ONLY_SUFFIX = {
    "pl": "plan tylko na dziś; jutro zostanie przeliczone automatycznie",
    "en": "today-only plan; tomorrow will be recalculated automatically",
}


def _state_number(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _state_attribute_number(
    hass: HomeAssistant,
    entity_id: str,
    attribute: str,
) -> float | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        return float(state.attributes[attribute])
    except (KeyError, TypeError, ValueError):
        return None


def _state_text(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
        return ""
    return state.state.strip()


def _helper_minutes(hass: HomeAssistant, entity_id: str) -> int | None:
    value = _state_text(hass, entity_id)
    parts = value.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _select_number(hass: HomeAssistant, entity_id: str) -> float | None:
    match = _NUMBER.search(_state_text(hass, entity_id).replace(",", "."))
    return float(match.group(0)) if match else None


def _first_numeric_state(
    hass: HomeAssistant,
    candidates: tuple[str, ...],
    configured: str = "",
) -> tuple[str, State | None]:
    entity_ids = ((configured,) if configured else ()) + candidates
    seen: set[str] = set()
    for entity_id in entity_ids:
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        state = hass.states.get(entity_id)
        if state is None:
            continue
        try:
            float(state.state)
        except (TypeError, ValueError):
            continue
        return entity_id, state
    return "", None


def _parse_datetime(value: Any, timezone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _state_age_minutes(state: State | None, now: datetime) -> float | None:
    """Return age of the latest HA state report without assuming its version."""
    if state is None:
        return None
    updated = getattr(state, "last_reported", None) or state.last_updated
    return max((now - updated.astimezone(now.tzinfo)).total_seconds() / 60.0, 0.0)


def _forecast_total(state: State | None, percentile: str) -> float | None:
    """Read Solcast P10/P50/P90 totals across old and new attribute layouts."""
    if state is None:
        return None
    if percentile == "p50":
        try:
            return max(float(state.state), 0.0)
        except (TypeError, ValueError):
            return None
    suffix = "10" if percentile == "p10" else "90"
    candidates = (f"estimate{suffix}", f"estimate{suffix}_kwh")
    for key in candidates:
        try:
            return max(float(state.attributes[key]), 0.0)
        except (KeyError, TypeError, ValueError):
            pass
    analysis = state.attributes.get("analysis")
    if isinstance(analysis, Mapping):
        for key in candidates:
            try:
                return max(float(analysis[key]), 0.0)
            except (KeyError, TypeError, ValueError):
                pass
    return None


def _detailed_pv_expected_elapsed_kwh(
    state: State | None,
    target_date: date,
    timezone: ZoneInfo,
    now: datetime,
) -> float | None:
    """Return raw P50 energy expected from midnight through ``now``."""
    if state is None:
        return None
    details = state.attributes.get("detailedForecast")
    if not isinstance(details, list):
        details = state.attributes.get("detailed_forecast")
    if not isinstance(details, list):
        return None
    expected = 0.0
    found = False
    for item in details:
        if not isinstance(item, Mapping):
            continue
        start = _parse_datetime(
            item.get("period_start") or item.get("period_start_local"),
            timezone,
        )
        if start is None or start.date() != target_date or start >= now:
            continue
        raw_power = (
            item.get("pv_estimate")
            if item.get("pv_estimate") is not None
            else item.get("estimate")
        )
        try:
            power_kw = max(float(raw_power), 0.0)
        except (TypeError, ValueError):
            continue
        fraction = min(
            max((now - start).total_seconds() / (30 * 60), 0.0),
            1.0,
        )
        expected += power_kw * 0.5 * fraction
        found = True
    return expected if found else None


def _detailed_pv_map(
    state: State | None,
    target_date: date,
    target_kwh: float,
    timezone: ZoneInfo,
    now_slot: datetime,
    *,
    percentile: str = "p50",
) -> dict[datetime, float]:
    if state is None or target_kwh <= 0:
        return {}
    details = state.attributes.get("detailedForecast")
    if not isinstance(details, list):
        details = state.attributes.get("detailed_forecast")
    if not isinstance(details, list):
        return {}
    values: dict[datetime, float] = {}
    for item in details:
        if not isinstance(item, Mapping):
            continue
        start = _parse_datetime(
            item.get("period_start") or item.get("period_start_local"),
            timezone,
        )
        if start is None or start.date() != target_date:
            continue
        start = floor_half_hour(start)
        if start < now_slot:
            continue
        if percentile == "p10":
            raw_power = (
                item.get("pv_estimate10")
                if item.get("pv_estimate10") is not None
                else item.get("estimate10")
            )
        elif percentile == "p90":
            raw_power = (
                item.get("pv_estimate90")
                if item.get("pv_estimate90") is not None
                else item.get("estimate90")
            )
        else:
            raw_power = (
                item.get("pv_estimate")
                if item.get("pv_estimate") is not None
                else item.get("estimate")
            )
        try:
            energy = max(float(raw_power), 0.0) * 0.5
        except (TypeError, ValueError):
            continue
        values[start] = values.get(start, 0.0) + energy
    total = sum(values.values())
    if total <= 0:
        return {}
    scale = target_kwh / total
    return {start: energy * scale for start, energy in values.items()}


def _blend_pv_maps(
    expected: Mapping[datetime, float],
    low: Mapping[datetime, float],
    risk_weight: float,
) -> dict[datetime, float]:
    """Blend P10/P50 maps while retaining slots absent from either series."""
    return {
        start: blend_low_expected(
            float(low.get(start, expected.get(start, 0.0))),
            float(expected.get(start, 0.0)),
            risk_weight,
        )
        for start in set(expected) | set(low)
    }


def _fallback_pv_map(
    target_date: date,
    target_kwh: float,
    timezone: ZoneInfo,
    now_slot: datetime,
    sunrise_minute: int,
    sunset_minute: int,
) -> dict[datetime, float]:
    if target_kwh <= 0:
        return {}
    starts: list[datetime] = []
    cursor = datetime.combine(target_date, time.min, tzinfo=timezone)
    for _ in range(48):
        minute = cursor.hour * 60 + cursor.minute
        if (
            cursor >= now_slot
            and sunrise_minute <= minute < sunset_minute
        ):
            starts.append(cursor)
        cursor += timedelta(minutes=30)
    if not starts:
        return {}
    per_slot = target_kwh / len(starts)
    return {start: per_slot for start in starts}


def _empty_load_summary() -> LoadHistorySummary:
    return LoadHistorySummary(
        average_daily_kwh=None,
        daily_history_days=0,
        daily_energy_kwh={},
        average_night_kwh=None,
        night_history_days=0,
        night_energy_kwh={},
        current_day_energy_kwh=None,
    )


class HoymilesRCEOptimizerSensor(SensorEntity):
    """Calculate a two-day, home-first RCE export plan."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "rce_optimized_plan"
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: RuntimeData,
    ) -> None:
        """Initialize the optimizer sensor."""
        self.hass = hass
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_rce_optimized_plan"
        self._result: OptimizerResult | None = None
        self._load_history = _empty_load_summary()
        self._extended_load_history = _empty_load_summary()
        self._full_history_refresh_date: date | None = None
        self._history_refresh_running = False
        self._forecast_accuracy_factor = 0.90
        self._forecast_accuracy_uncertainty = 0.15
        self._forecast_accuracy_days = 0
        self._forecast_accuracy_source = "automatic_conservative_fallback"
        self._forecast_refresh_running = False
        self._forecast_refresh_date: date | None = None
        self._recalculate_cancel = None
        self._attributes: dict[str, Any] = {
            "status_code": "missing_data",
            "missing_entities": [],
            "planned_slots": [],
        }

    @property
    def suggested_object_id(self) -> str:
        """Return a stable entity id."""
        return "hoymiles_hit_rce_optimized_plan"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the optimizer to the localized inverter device."""
        source = self._runtime.source_device
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=source.name_by_user or source.name or NAME,
            manufacturer=source.manufacturer or "Hoymiles",
            model=source.model or "HIT xxL G3",
            sw_version=source.sw_version,
        )

    @property
    def native_value(self) -> str:
        """Return a localized plan state."""
        language = "pl" if self.hass.config.language.startswith("pl") else "en"
        code = str(self._attributes.get("status_code", "missing_data"))
        text = STATUS_TEXT[language].get(
            code,
            STATUS_TEXT[language]["optimizer_error"],
        )
        if (
            self._attributes.get("planning_scope") == "today_only"
            and code in {"ready", "waiting_for_market", "home_protected"}
        ):
            return f"{text} — {TODAY_ONLY_SUFFIX[language]}"
        return text

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return plan diagnostics used by the dashboard and automations."""
        return self._attributes

    async def async_added_to_hass(self) -> None:
        """Track every input that can change the plan."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                sorted(WATCHED_ENTITIES),
                self._async_input_changed,
            )
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_timer,
                timedelta(minutes=1),
            )
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_history_timer,
                timedelta(hours=1),
            )
        )
        await self._async_refresh_load_history(force_full=True)
        await self._async_refresh_forecast_accuracy(force=True)
        self._recalculate()
        self.async_write_ha_state()

    async def _async_history_timer(self, now: datetime) -> None:
        """Refresh recorder-backed LOAD history once per hour."""
        await self._async_refresh_load_history()
        await self._async_refresh_forecast_accuracy()
        self._recalculate_and_write()

    async def _async_refresh_load_history(self, *, force_full: bool = False) -> None:
        """Refresh LOAD history without scanning 28 raw days every hour."""
        if self._history_refresh_running:
            return
        self._history_refresh_running = True
        try:
            timezone = ZoneInfo(self.hass.config.time_zone)
            now = datetime.now(timezone)
            full_refresh = (
                force_full or self._full_history_refresh_date != now.date()
            )
            local_start = datetime.combine(
                now.date() - timedelta(days=31 if full_refresh else 0),
                time.min,
                tzinfo=timezone,
            )
            query = partial(
                recorder_history.get_significant_states,
                self.hass,
                dt_util.as_utc(local_start),
                dt_util.as_utc(now),
                list(LOAD_PHASE_ENERGY_ENTITIES),
                None,
                True,
                False,
                False,
                True,
            )
            raw_history = await get_recorder_instance(
                self.hass
            ).async_add_executor_job(query)

            samples: dict[str, list[tuple[datetime, float]]] = {
                entity_id: [] for entity_id in LOAD_PHASE_ENERGY_ENTITIES
            }
            for entity_id in LOAD_PHASE_ENERGY_ENTITIES:
                for item in raw_history.get(entity_id, []):
                    state_value = getattr(item, "state", None)
                    updated = getattr(item, "last_updated", None)
                    if state_value is None or updated is None:
                        continue
                    try:
                        numeric = float(state_value)
                    except (TypeError, ValueError):
                        continue
                    samples[entity_id].append(
                        (updated.astimezone(timezone), max(numeric, 0.0))
                    )

            night_windows: dict[date, tuple[datetime, datetime]] = {}
            for offset in range(29 if full_refresh else 0, 0, -1):
                night_date = now.date() - timedelta(days=offset)
                sunset = get_astral_event_date(
                    self.hass,
                    "sunset",
                    night_date,
                )
                sunrise = get_astral_event_date(
                    self.hass,
                    "sunrise",
                    night_date + timedelta(days=1),
                )
                if sunset is None or sunrise is None:
                    continue
                night_windows[night_date] = (
                    sunset.astimezone(timezone) - timedelta(minutes=90),
                    sunrise.astimezone(timezone) + timedelta(minutes=90),
                )

            current_day_window: tuple[datetime, datetime] | None = None
            today_sunrise = get_astral_event_date(
                self.hass,
                "sunrise",
                now.date(),
            )
            today_sunset = get_astral_event_date(
                self.hass,
                "sunset",
                now.date(),
            )
            if today_sunrise is not None and today_sunset is not None:
                current_day_start = (
                    today_sunrise.astimezone(timezone)
                    + timedelta(minutes=90)
                )
                current_day_end = min(
                    now,
                    today_sunset.astimezone(timezone)
                    - timedelta(minutes=90),
                )
                if current_day_end > current_day_start:
                    current_day_window = (
                        current_day_start,
                        current_day_end,
                    )

            if full_refresh:
                self._load_history = summarize_load_history(
                    samples,
                    now=now,
                    night_windows=night_windows,
                    current_day_window=current_day_window,
                    history_days=4,
                )
                self._extended_load_history = summarize_load_history(
                    samples,
                    now=now,
                    night_windows=night_windows,
                    current_day_window=current_day_window,
                    history_days=28,
                )
                self._full_history_refresh_date = now.date()
            else:
                current = summarize_load_history(
                    samples,
                    now=now,
                    night_windows={},
                    current_day_window=current_day_window,
                    history_days=0,
                )
                self._load_history = replace(
                    self._load_history,
                    current_day_energy_kwh=current.current_day_energy_kwh,
                )
                self._extended_load_history = replace(
                    self._extended_load_history,
                    current_day_energy_kwh=current.current_day_energy_kwh,
                )
        except Exception:  # noqa: BLE001 - recorder outages need a safe fallback
            _LOGGER.exception("Cannot rebuild recorder-backed LOAD history")
        finally:
            self._history_refresh_running = False

    async def _async_refresh_forecast_accuracy(
        self,
        *,
        force: bool = False,
    ) -> None:
        """Learn a robust conservative Solcast factor once per local day."""
        if self._forecast_refresh_running:
            return
        timezone = ZoneInfo(self.hass.config.time_zone)
        now = dt_util.now().astimezone(timezone)
        if not force and self._forecast_refresh_date == now.date():
            return
        self._forecast_refresh_running = True
        try:
            configured = _state_text(
                self.hass,
                "input_text.hoymiles_solcast_forecast_today_entity",
            )
            forecast_entity, forecast_state = _first_numeric_state(
                self.hass,
                TODAY_FORECAST_CANDIDATES,
                configured,
            )
            if not forecast_entity or forecast_state is None:
                return
            actual_entity = "sensor.hoymiles_hit_pv_total_energy_today"
            start = now - timedelta(days=15)
            query = partial(
                recorder_history.get_significant_states,
                self.hass,
                dt_util.as_utc(start),
                dt_util.as_utc(now),
                [forecast_entity, actual_entity],
                None,
                True,
                False,
                False,
                True,
            )
            raw = await get_recorder_instance(
                self.hass
            ).async_add_executor_job(query)
            forecast_by_day: dict[date, list[float]] = {}
            actual_by_day: dict[date, list[float]] = {}
            for entity_id, destination in (
                (forecast_entity, forecast_by_day),
                (actual_entity, actual_by_day),
            ):
                for item in raw.get(entity_id, []):
                    updated = getattr(item, "last_updated", None)
                    value = getattr(item, "state", None)
                    if updated is None or value is None:
                        continue
                    try:
                        numeric = max(float(value), 0.0)
                    except (TypeError, ValueError):
                        continue
                    local_day = updated.astimezone(timezone).date()
                    if local_day >= now.date():
                        continue
                    destination.setdefault(local_day, []).append(numeric)

            samples: list[tuple[float, float]] = []
            for day in sorted(set(forecast_by_day) & set(actual_by_day)):
                forecasts = [value for value in forecast_by_day[day] if value > 0.5]
                actuals = actual_by_day[day]
                if not forecasts or not actuals:
                    continue
                # The median is robust to several intraday Solcast refreshes.
                ordered = sorted(forecasts)
                forecast = ordered[len(ordered) // 2]
                actual = max(actuals)
                if actual <= 0.5:
                    continue
                age_days = float((now.date() - day).days)
                samples.append((age_days, actual / forecast))
            factor, uncertainty, count = robust_weighted_factor(samples)
            self._forecast_accuracy_factor = factor
            self._forecast_accuracy_uncertainty = uncertainty
            self._forecast_accuracy_days = count
            self._forecast_accuracy_source = (
                "recorder_actual_vs_solcast_robust"
                if count
                else "automatic_conservative_fallback"
            )
            self._forecast_refresh_date = now.date()
        except Exception:  # noqa: BLE001 - safe fallback remains active
            _LOGGER.exception("Cannot learn RCE Solcast forecast accuracy")
        finally:
            self._forecast_refresh_running = False

    @callback
    def _async_input_changed(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Coalesce fast ESPHome updates into one optimizer refresh."""
        if self._recalculate_cancel is not None:
            self._recalculate_cancel()
        self._recalculate_cancel = async_call_later(
            self.hass,
            5,
            self._async_debounced_recalculate,
        )

    @callback
    def _async_debounced_recalculate(self, now: datetime) -> None:
        self._recalculate_cancel = None
        self._recalculate_and_write()

    @callback
    def _async_timer(self, now: datetime) -> None:
        """Refresh the active slot and rolling forecast every minute."""
        if self._recalculate_cancel is not None:
            self._recalculate_cancel()
            self._recalculate_cancel = None
        self._recalculate_and_write()

    def _recalculate_and_write(self) -> None:
        """Write to HA only when the material plan state changed."""
        previous_state = self.native_value
        previous_attributes = self._attributes
        self._recalculate()
        if previous_state != self.native_value or previous_attributes != self._attributes:
            self.async_write_ha_state()

    def _recalculate(self) -> None:
        try:
            settings, metadata = self._optimizer_input()
            if settings is None:
                self._result = None
                self._attributes = {
                    "status_code": "missing_data",
                    "missing_entities": metadata["missing_entities"],
                    "planned_slots": [],
                    **metadata,
                }
                return
            result = optimize_rce(settings)
            self._result = result
            current_slot = floor_half_hour(settings.now)
            planned_slots = [
                {
                    "date": item.start.date().isoformat(),
                    "start": item.start.strftime("%H:%M"),
                    "end": (item.start + timedelta(minutes=30)).strftime("%H:%M"),
                    "price": round(item.price_pln_kwh, 4),
                    "energy": round(item.energy_kwh, 2),
                    "revenue": round(item.revenue_pln, 2),
                }
                for item in result.planned_exports
            ]
            self._attributes = {
                "status_code": result.status_code,
                "missing_entities": [],
                "minimum_soc": result.minimum_soc_percent,
                "base_reserve_energy_kwh": round(
                    result.base_reserve_energy_kwh,
                    2,
                ),
                "protected_night_energy_kwh": round(
                    result.protected_night_energy_kwh,
                    2,
                ),
                "additional_forecast_reserve_kwh": round(
                    result.additional_forecast_reserve_kwh,
                    2,
                ),
                "protected_home_energy_kwh": round(
                    result.protected_home_energy_kwh,
                    2,
                ),
                "available_energy_now_kwh": round(
                    result.available_energy_now_kwh,
                    2,
                ),
                "current_battery_energy_kwh": round(
                    settings.battery_capacity_kwh
                    * settings.battery_soc_percent
                    / 100.0,
                    2,
                ),
                "planned_export_kwh": round(result.planned_export_kwh, 2),
                "planned_revenue_pln": round(result.planned_revenue_pln, 2),
                "automatic_price_floor_pln_kwh": (
                    round(result.automatic_price_floor_pln_kwh, 4)
                    if result.automatic_price_floor_pln_kwh is not None
                    else None
                ),
                "highest_planned_price_pln_kwh": (
                    round(
                        max(
                            item.price_pln_kwh
                            for item in result.planned_exports
                        ),
                        4,
                    )
                    if result.planned_exports
                    else None
                ),
                "natural_pv_export_kwh": round(result.natural_export_kwh, 2),
                "natural_pv_revenue_pln": round(
                    result.natural_revenue_pln,
                    2,
                ),
                "expected_total_export_kwh": round(result.total_export_kwh, 2),
                "estimated_revenue_pln": round(result.total_revenue_pln, 2),
                "uncontrolled_export_kwh": round(
                    result.uncontrolled_export_kwh,
                    2,
                ),
                "uncontrolled_revenue_pln": round(
                    result.uncontrolled_revenue_pln,
                    2,
                ),
                "optimization_gain_pln": round(
                    result.optimization_gain_pln,
                    2,
                ),
                "gross_optimization_gain_pln": round(
                    result.gross_optimization_gain_pln,
                    2,
                ),
                "gross_optimization_gain_basis": (
                    "optimized_market_revenue_minus_uncontrolled_market_revenue"
                ),
                "optimization_gain_basis": "gross_revenue_uplift",
                "optimization_gain_legacy_alias": (
                    "gross_optimization_gain_pln"
                ),
                "ending_battery_kwh": round(result.ending_battery_kwh, 2),
                "ending_battery_soc": round(
                    result.ending_battery_kwh
                    / max(settings.battery_capacity_kwh, 0.001)
                    * 100.0,
                    1,
                ),
                "system_power_kw": round(result.system_power_kw, 2),
                "requested_export_power_kw": round(
                    result.requested_export_power_kw,
                    2,
                ),
                "bms_discharge_power_limit_kw": (
                    round(result.bms_discharge_power_limit_kw, 2)
                    if result.bms_discharge_power_limit_kw is not None
                    else None
                ),
                "bms_discharge_limit_percent": (
                    round(result.bms_discharge_limit_percent, 1)
                    if result.bms_discharge_limit_percent is not None
                    else None
                ),
                "bms_limit_active": result.bms_limit_active,
                "maximum_export_power_kw": round(
                    result.maximum_export_power_kw,
                    2,
                ),
                "export_power_cap_kw": (
                    round(result.export_power_cap_kw, 2)
                    if result.export_power_cap_kw is not None
                    else None
                ),
                "effective_export_power_kw": (
                    round(result.effective_export_power_kw, 2)
                    if result.effective_export_power_kw is not None
                    else None
                ),
                "physical_limit_source": result.physical_limit_source,
                "load_profile_mode": result.load_profile_mode,
                "forecast_confidence_percent": round(
                    result.forecast_confidence_percent,
                    1,
                ),
                "battery_wear_cost_pln": round(
                    result.battery_wear_cost_pln,
                    2,
                ),
                "control_reserve_energy_kwh": round(
                    result.control_reserve_energy_kwh,
                    2,
                ),
                "soc_quantization_reserve_kwh": round(
                    result.soc_quantization_reserve_kwh,
                    2,
                ),
                "day3_forecast_available": result.day3_forecast_available,
                "day3_forecast_kwh": (
                    round(result.day3_forecast_kwh, 2)
                    if result.day3_forecast_kwh is not None
                    else None
                ),
                "day3_load_requirement_kwh": round(
                    result.day3_load_requirement_kwh,
                    2,
                ),
                "day3_energy_shortfall_kwh": round(
                    result.day3_energy_shortfall_kwh,
                    2,
                ),
                "terminal_reserve_reason": result.terminal_reserve_reason,
                "terminal_energy_target_reason": result.terminal_reserve_reason,
                "terminal_energy_target_kwh": round(
                    result.terminal_energy_target_kwh,
                    2,
                ),
                "terminal_energy_value_pln_kwh": round(
                    result.terminal_energy_value_pln_kwh,
                    4,
                ),
                "terminal_energy_value_pln": round(
                    result.terminal_energy_value_pln,
                    2,
                ),
                "baseline_terminal_energy_value_pln": round(
                    result.baseline_terminal_energy_value_pln,
                    2,
                ),
                "terminal_energy_value_delta_pln": round(
                    result.terminal_energy_value_delta_pln,
                    2,
                ),
                "net_objective_pln": round(result.net_objective_pln, 2),
                "net_optimization_gain_pln": round(
                    result.net_optimization_gain_pln,
                    2,
                ),
                "net_optimization_gain_basis": (
                    "gross_gain_minus_battery_wear_plus_terminal_value_delta"
                ),
                "historical_day_load_kwh": round(
                    result.historical_day_load_kwh,
                    2,
                ),
                "live_projected_day_load_kwh": round(
                    result.live_projected_day_load_kwh,
                    2,
                ),
                "modeled_day_load_kwh": round(
                    result.modeled_day_load_kwh,
                    2,
                ),
                "daylight_progress_percent": round(
                    result.daylight_progress_percent,
                    1,
                ),
                "current_slot_planned": any(
                    item.start == current_slot for item in result.planned_exports
                ),
                "planned_slots": planned_slots,
                **metadata,
            }
        except Exception:  # noqa: BLE001 - fail closed in the automation entity
            _LOGGER.exception("Cannot calculate the optimized RCE plan")
            self._result = None
            self._attributes = {
                "status_code": "optimizer_error",
                "missing_entities": [],
                "planned_slots": [],
            }

    def _optimizer_input(
        self,
    ) -> tuple[OptimizerInput | None, dict[str, Any]]:
        timezone = ZoneInfo(self.hass.config.time_zone)
        now = dt_util.now().astimezone(timezone)
        now_slot = floor_half_hour(now)

        required = {
            "sensor.hoymiles_hit_battery_capacity": _state_number(
                self.hass,
                "sensor.hoymiles_hit_battery_capacity",
            ),
            "sensor.hoymiles_hit_overview_battery_soc": _state_number(
                self.hass,
                "sensor.hoymiles_hit_overview_battery_soc",
            ),
            "number.hoymiles_hit_self_use_soc": _state_number(
                self.hass,
                "number.hoymiles_hit_self_use_soc",
            ),
            "number.hoymiles_hit_force_discharge_soc": _state_number(
                self.hass,
                "number.hoymiles_hit_force_discharge_soc",
            ),
            "input_number.hoymiles_rce_requested_discharge_power": _state_number(
                self.hass,
                "input_number.hoymiles_rce_requested_discharge_power",
            ),
            "input_number.hoymiles_rce_soc_safety_margin": _state_number(
                self.hass,
                "input_number.hoymiles_rce_soc_safety_margin",
            ),
            "input_number.hoymiles_rce_export_efficiency": _state_number(
                self.hass,
                "input_number.hoymiles_rce_export_efficiency",
            ),
        }
        rated_power = _select_number(
            self.hass,
            "input_select.hoymiles_rce_inverter_rated_power",
        )
        if rated_power is None:
            required["input_select.hoymiles_rce_inverter_rated_power"] = None

        sun = self.hass.states.get("sun.sun")
        rising = (
            _parse_datetime(sun.attributes.get("next_rising"), timezone)
            if sun
            else None
        )
        setting = (
            _parse_datetime(sun.attributes.get("next_setting"), timezone)
            if sun
            else None
        )
        if rising is None or setting is None:
            required["sun.sun"] = None

        today_rows_state = self.hass.states.get("sensor.hoymiles_rce_day")
        tomorrow_rows_state = self.hass.states.get(
            "sensor.hoymiles_rce_day_tomorrow"
        )
        today_rows = (
            today_rows_state.attributes.get("value", [])
            if today_rows_state
            else []
        )
        tomorrow_rows = (
            tomorrow_rows_state.attributes.get("value", [])
            if tomorrow_rows_state
            else []
        )
        tomorrow_rows_complete = (
            isinstance(tomorrow_rows, list)
            and len(tomorrow_rows) >= _MIN_COMPLETE_RCE_DAY_PERIODS
        )
        usable_tomorrow_rows = tomorrow_rows if tomorrow_rows_complete else []
        if not isinstance(today_rows, list) or not today_rows:
            required["sensor.hoymiles_rce_day"] = None

        block_start = _helper_minutes(
            self.hass,
            "input_datetime.hoymiles_sale_block_start",
        )
        block_end = _helper_minutes(
            self.hass,
            "input_datetime.hoymiles_sale_block_end",
        )
        if block_start is None:
            required["input_datetime.hoymiles_sale_block_start"] = None
        if block_end is None:
            required["input_datetime.hoymiles_sale_block_end"] = None

        today_configured = _state_text(
            self.hass,
            "input_text.hoymiles_solcast_forecast_today_entity",
        )
        tomorrow_configured = _state_text(
            self.hass,
            "input_text.hoymiles_solcast_forecast_tomorrow_entity",
        )
        today_entity, today_forecast_state = _first_numeric_state(
            self.hass,
            TODAY_FORECAST_CANDIDATES,
            today_configured,
        )
        tomorrow_entity, tomorrow_forecast_state = _first_numeric_state(
            self.hass,
            TOMORROW_FORECAST_CANDIDATES,
            tomorrow_configured,
        )
        remaining_entity, remaining_state = _first_numeric_state(
            self.hass,
            REMAINING_TODAY_CANDIDATES,
        )
        day3_entity, day3_forecast_state = _first_numeric_state(
            self.hass,
            DAY3_FORECAST_CANDIDATES,
        )
        if today_forecast_state is None:
            required["Solcast Forecast Today"] = None
        if tomorrow_forecast_state is None:
            required["Solcast Forecast Tomorrow"] = None

        if self._load_history.average_daily_kwh is not None:
            history_load = self._load_history.average_daily_kwh
            load_history_days = float(self._load_history.daily_history_days)
            load_history_source = "recorder_phase_energy_counters"
        else:
            history_load = _state_number(
                self.hass,
                "sensor.hoymiles_load_average_4_days",
            )
            load_history_days = _state_attribute_number(
                self.hass,
                "sensor.hoymiles_load_average_4_days",
                "history_days",
            )
            load_history_source = "statistics_fallback"
        if self._load_history.average_night_kwh is not None:
            night_history_days = float(self._load_history.night_history_days)
        else:
            night_history_days = _state_attribute_number(
                self.hass,
                "sensor.hoymiles_night_load_average_4_days",
                "history_days",
            )
        fallback_load = _state_number(
            self.hass,
            "input_number.hoymiles_rce_fallback_daily_load",
        )
        actual_load_today = _state_number(
            self.hass,
            "sensor.hoymiles_actual_load_energy_today",
        )
        # A newly installed/migrated actual-load meter needs four complete days
        # before the long-term statistic is representative.  During that
        # transition, never reserve less than either the user fallback or a
        # conservative projection of today's measured house energy.  The
        # quarter-day denominator prevents one early counter step from creating
        # an unrealistically large projection just after midnight.
        elapsed_day_fraction = max(
            (now.hour * 60 + now.minute) / (24 * 60),
            0.25,
        )
        live_daily_projection = (
            max(actual_load_today, 0.0) / elapsed_day_fraction
            if actual_load_today is not None
            else None
        )
        history_complete = (load_history_days or 0.0) >= 3.95
        if history_load is not None and history_complete:
            average_load = history_load
            load_model_source = "history_4_days"
        else:
            provisional_candidates = (
                history_load,
                fallback_load,
                live_daily_projection,
            )
            numeric_candidates = [
                value for value in provisional_candidates if value is not None
            ]
            average_load = max(numeric_candidates) if numeric_candidates else None
            load_model_source = "provisional_safe_max"
        if average_load is None:
            required["sensor.hoymiles_load_average_4_days"] = None

        missing = sorted(
            entity_id for entity_id, value in required.items() if value is None
        )
        profile_history = (
            self._extended_load_history
            if self._extended_load_history.daily_history_days
            else self._load_history
        )
        metadata: dict[str, Any] = {
            "missing_entities": missing,
            "forecast_today_entity": today_entity or "none",
            "forecast_tomorrow_entity": tomorrow_entity or "none",
            "forecast_remaining_today_entity": remaining_entity or "fallback",
            "forecast_day3_entity": day3_entity or "not_enabled",
            "rce_today_periods": len(today_rows) if isinstance(today_rows, list) else 0,
            "rce_tomorrow_periods": (
                len(tomorrow_rows) if isinstance(tomorrow_rows, list) else 0
            ),
            "planning_scope": (
                "today_and_tomorrow"
                if tomorrow_rows_complete
                else "today_only"
            ),
            "tomorrow_data_pending": not tomorrow_rows_complete,
            "automatic_replan": True,
            "automatic_discharge_enabled": self.hass.states.is_state(
                "input_boolean.hoymiles_rce_discharge_enabled",
                "on",
            ),
            "plan_is_preview": not self.hass.states.is_state(
                "input_boolean.hoymiles_rce_discharge_enabled",
                "on",
            ),
            "load_model_source": load_model_source,
            "load_history_source": load_history_source,
            "recorder_load_average_4d_kwh": (
                round(self._load_history.average_daily_kwh, 2)
                if self._load_history.average_daily_kwh is not None
                else None
            ),
            "recorder_load_history_days": profile_history.daily_history_days,
            "recorder_load_history_energy_kwh": round(
                profile_history.daily_energy_total_kwh,
                2,
            ),
            "recorder_load_daily_kwh": profile_history.daily_energy_kwh,
            "recorder_load_recent_4d_kwh": self._load_history.daily_energy_kwh,
            "recorder_load_average_28d_kwh": (
                round(profile_history.average_daily_kwh, 2)
                if profile_history.average_daily_kwh is not None
                else None
            ),
            "recorder_load_profile_30m_kwh": list(
                profile_history.average_profile_kwh
            ),
            "recorder_load_average_profile_30m_kwh": list(
                profile_history.average_profile_kwh
            ),
            "recorder_load_profile_history_days": (
                profile_history.daily_history_days
            ),
            "recorder_load_weekday_profile_30m_kwh": list(
                profile_history.weekday_profile_kwh
            ),
            "recorder_load_weekend_profile_30m_kwh": list(
                profile_history.weekend_profile_kwh
            ),
            "recorder_load_weekday_profile_days": (
                profile_history.weekday_profile_days
            ),
            "recorder_load_weekend_profile_days": (
                profile_history.weekend_profile_days
            ),
            "recorder_night_load_average_4d_kwh": (
                round(self._load_history.average_night_kwh, 2)
                if self._load_history.average_night_kwh is not None
                else None
            ),
            "recorder_night_history_days": self._load_history.night_history_days,
            "recorder_night_history_energy_kwh": round(
                self._load_history.night_energy_total_kwh,
                2,
            ),
            "recorder_night_daily_kwh": self._load_history.night_energy_kwh,
            "recorder_night_daily_kwh_28d": (
                profile_history.night_energy_kwh
            ),
            "recorder_night_load_average_28d_kwh": (
                round(profile_history.average_night_kwh, 2)
                if profile_history.average_night_kwh is not None
                else None
            ),
            "actual_load_energy_today_kwh": (
                round(actual_load_today, 2)
                if actual_load_today is not None
                else None
            ),
            "actual_day_window_load_today_kwh": (
                round(self._load_history.current_day_energy_kwh, 2)
                if self._load_history.current_day_energy_kwh is not None
                else None
            ),
            "day_load_live_source": (
                "recorder_actual_phase_load"
                if self._load_history.current_day_energy_kwh is not None
                else "history_only"
            ),
            "provisional_daily_load_projection_kwh": (
                round(live_daily_projection, 2)
                if live_daily_projection is not None
                else None
            ),
            "selected_average_daily_load_kwh": (
                round(average_load, 2) if average_load is not None else None
            ),
            "load_history_days": (
                round(load_history_days, 2)
                if load_history_days is not None
                else 0.0
            ),
            "night_history_days": (
                round(night_history_days, 2)
                if night_history_days is not None
                else 0.0
            ),
            "history_complete": (
                history_complete and (night_history_days or 0.0) >= 3.95
            ),
        }
        if missing:
            return None, metadata

        assert rising is not None
        assert setting is not None
        assert block_start is not None
        assert block_end is not None
        assert average_load is not None
        assert rated_power is not None
        price_slots = parse_rce_rows(
            [*today_rows, *usable_tomorrow_rows],
            timezone,
            block_enabled=self.hass.states.is_state(
                "input_boolean.hoymiles_sale_block_enabled",
                "on",
            ),
            block_start_minute=block_start,
            block_end_minute=block_end,
        )

        forecast_today_raw = max(float(today_forecast_state.state), 0.0)
        forecast_tomorrow_raw = max(float(tomorrow_forecast_state.state), 0.0)
        actual_pv_today = _state_number(
            self.hass,
            "sensor.hoymiles_hit_pv_total_energy_today",
        ) or 0.0
        remaining_today_raw = (
            max(float(remaining_state.state), 0.0)
            if remaining_state is not None
            else max(forecast_today_raw - actual_pv_today, 0.0)
        )
        sunrise_minute = rising.hour * 60 + rising.minute
        sunset_minute = setting.hour * 60 + setting.minute
        night_start = (sunset_minute - 90) % (24 * 60)
        night_end = (sunrise_minute + 90) % (24 * 60)

        sunrise_today = get_astral_event_date(
            self.hass,
            "sunrise",
            now.date(),
        )
        sunset_today = get_astral_event_date(
            self.hass,
            "sunset",
            now.date(),
        )
        expected_elapsed_raw = _detailed_pv_expected_elapsed_kwh(
            today_forecast_state,
            now.date(),
            timezone,
            now,
        )
        live_eligible = (
            sunrise_today is not None
            and sunset_today is not None
            and now >= sunrise_today.astimezone(timezone) + timedelta(minutes=90)
            and now <= sunset_today.astimezone(timezone) + timedelta(minutes=30)
        )
        (
            today_forecast_factor,
            live_forecast_ratio,
            live_forecast_confidence,
        ) = adaptive_forecast_factor(
            self._forecast_accuracy_factor,
            actual_pv_today,
            expected_elapsed_raw,
            eligible=live_eligible,
        )
        forecast_today = forecast_today_raw * today_forecast_factor
        forecast_tomorrow = (
            forecast_tomorrow_raw * self._forecast_accuracy_factor
        )
        remaining_today = remaining_today_raw * today_forecast_factor

        today_p10_raw = _forecast_total(today_forecast_state, "p10")
        today_p90_raw = _forecast_total(today_forecast_state, "p90")
        tomorrow_p10_raw = _forecast_total(tomorrow_forecast_state, "p10")
        tomorrow_p90_raw = _forecast_total(tomorrow_forecast_state, "p90")
        uncertainty_available = (
            today_p10_raw is not None
            and tomorrow_p10_raw is not None
            and forecast_today_raw > 0
            and forecast_tomorrow_raw > 0
        )
        risk_weight = uncertainty_risk_weight(
            history_days=self._forecast_accuracy_days,
            live_confidence=live_forecast_confidence,
            uncertainty_available=uncertainty_available,
        )
        uncertainty_spread_ratio = (
            max(tomorrow_p90_raw - tomorrow_p10_raw, 0.0)
            / max(forecast_tomorrow_raw, 0.001)
            if tomorrow_p90_raw is not None
            and tomorrow_p10_raw is not None
            and forecast_tomorrow_raw > 0
            else 0.0
        )
        # A wider P10–P90 band moves reserve planning further toward P10.
        risk_weight = min(
            risk_weight + min(uncertainty_spread_ratio, 1.0) * 0.15,
            0.90,
        )
        analysis = tomorrow_forecast_state.attributes.get("analysis")
        solcast_band_confidence: float | None = None
        if isinstance(analysis, Mapping):
            try:
                solcast_band_confidence = min(
                    max(float(analysis["confidence"]), 0.0),
                    1.0,
                )
            except (KeyError, TypeError, ValueError):
                pass
        history_forecast_confidence = min(
            self._forecast_accuracy_days / 4.0,
            1.0,
        )
        forecast_confidence = 100.0 * (
            0.50 * history_forecast_confidence
            + 0.30 * (solcast_band_confidence or 0.0)
            + 0.20 * live_forecast_confidence
        )
        today_p10_remaining = (
            remaining_today
            * min(max(today_p10_raw / forecast_today_raw, 0.0), 1.0)
            if today_p10_raw is not None and forecast_today_raw > 0
            else remaining_today
        )
        tomorrow_p10 = (
            forecast_tomorrow
            * min(max(tomorrow_p10_raw / forecast_tomorrow_raw, 0.0), 1.0)
            if tomorrow_p10_raw is not None and forecast_tomorrow_raw > 0
            else forecast_tomorrow
        )

        pv_today = _detailed_pv_map(
            today_forecast_state,
            now.date(),
            remaining_today,
            timezone,
            now_slot,
        )
        if not pv_today:
            pv_today = _fallback_pv_map(
                now.date(),
                remaining_today,
                timezone,
                now_slot,
                sunrise_minute,
                sunset_minute,
            )
        pv_today_low = _detailed_pv_map(
            today_forecast_state,
            now.date(),
            today_p10_remaining,
            timezone,
            now_slot,
            percentile="p10",
        )
        if not pv_today_low:
            pv_today_low = _fallback_pv_map(
                now.date(),
                today_p10_remaining,
                timezone,
                now_slot,
                sunrise_minute,
                sunset_minute,
            )
        tomorrow_date = now.date() + timedelta(days=1)
        pv_tomorrow = _detailed_pv_map(
            tomorrow_forecast_state,
            tomorrow_date,
            forecast_tomorrow,
            timezone,
            now_slot,
        )
        if not pv_tomorrow:
            pv_tomorrow = _fallback_pv_map(
                tomorrow_date,
                forecast_tomorrow,
                timezone,
                now_slot,
                sunrise_minute,
                sunset_minute,
            )
        pv_tomorrow_low = _detailed_pv_map(
            tomorrow_forecast_state,
            tomorrow_date,
            tomorrow_p10,
            timezone,
            now_slot,
            percentile="p10",
        )
        if not pv_tomorrow_low:
            pv_tomorrow_low = _fallback_pv_map(
                tomorrow_date,
                tomorrow_p10,
                timezone,
                now_slot,
                sunrise_minute,
                sunset_minute,
            )
        pv_by_slot = dict(pv_today)
        for start, energy in pv_tomorrow.items():
            pv_by_slot[start] = pv_by_slot.get(start, 0.0) + energy
        low_pv_by_slot = dict(pv_today_low)
        for start, energy in pv_tomorrow_low.items():
            low_pv_by_slot[start] = low_pv_by_slot.get(start, 0.0) + energy
        conservative_pv_by_slot = _blend_pv_maps(
            pv_by_slot,
            low_pv_by_slot,
            risk_weight,
        )

        day3_raw = _forecast_total(day3_forecast_state, "p50")
        day3_p10_raw = _forecast_total(day3_forecast_state, "p10")
        day3_expected = (
            day3_raw * self._forecast_accuracy_factor
            if day3_raw is not None
            else None
        )
        day3_low = (
            day3_p10_raw * self._forecast_accuracy_factor
            if day3_p10_raw is not None
            else day3_expected
        )
        day3_conservative = (
            blend_low_expected(day3_low or 0.0, day3_expected, risk_weight)
            if day3_expected is not None
            else None
        )

        inverter_count_raw = _state_number(
            self.hass,
            "sensor.hoymiles_hit_number_of_machines_master_and_slave",
        )
        inverter_count = min(
            max(round(inverter_count_raw or 1.0), 1),
            10,
        )
        system_power_kw = rated_power * inverter_count
        gcf_enabled = _state_text(
            self.hass,
            "select.hoymiles_hit_generation_control_function",
        ).lower() in {"enabled", "on", "active", "włączone", "wlaczone"}
        gcf_limit_percent = _state_number(
            self.hass,
            "number.hoymiles_hit_maximum_export_power_limit",
        )
        export_power_cap_kw = (
            system_power_kw
            * min(max(gcf_limit_percent or 0.0, 0.0), 100.0)
            / 100.0
            if gcf_enabled and gcf_limit_percent is not None
            else None
        )
        effective_export_power_kw: float | None = None
        effective_export_source = "not_available"
        # Never interpret live Grid=0 in Self-Use as an inverter limit.  Only
        # an explicit learned/effective entity may constrain future planning.
        for entity_id in (
            "sensor.hoymiles_rce_learned_export_power",
            "sensor.hoymiles_rce_effective_export_power",
        ):
            learned = _state_number(self.hass, entity_id)
            if learned is not None and learned > 0:
                effective_export_power_kw = learned
                effective_export_source = entity_id
                break

        tariff_plan = self.hass.states.get(
            "sensor.hoymiles_hit_tariff_charge_plan"
        )
        avoided_import_price = None
        if tariff_plan is not None:
            for attribute in (
                "tariff_profile_g11_price",
                "g11_reference_price_pln_kwh",
                "current_price_pln_kwh",
            ):
                try:
                    avoided_import_price = float(
                        tariff_plan.attributes[attribute]
                    )
                    break
                except (KeyError, TypeError, ValueError):
                    pass
        if avoided_import_price is None:
            avoided_import_price = _state_number(
                self.hass,
                "input_number.hoymiles_tariff_g11_price",
            )
        if avoided_import_price is None or avoided_import_price <= 0:
            avoided_import_price = 1.0
        battery_wear_cost = _state_number(
            self.hass,
            "input_number.hoymiles_rce_battery_wear_cost",
        )
        if battery_wear_cost is None:
            battery_wear_cost = 0.08
        charge_efficiency = _state_number(
            self.hass,
            "input_number.hoymiles_tariff_charge_efficiency",
        )
        discharge_efficiency = _state_number(
            self.hass,
            "input_number.hoymiles_tariff_discharge_efficiency",
        )
        charge_efficiency = charge_efficiency or 95.0
        discharge_efficiency = discharge_efficiency or 95.0
        night_load = self._load_history.average_night_kwh
        if night_load is None:
            night_load = _state_number(
                self.hass,
                "sensor.hoymiles_night_load_average_4_days",
            )
        pv_to_load_today = _state_number(
            self.hass,
            "sensor.hoymiles_hit_pv_to_load_energy_today",
        ) or 0.0
        battery_to_load_today = _state_number(
            self.hass,
            "sensor.hoymiles_hit_energy_from_battery_today",
        ) or 0.0
        grid_to_load_today = _state_number(
            self.hass,
            "sensor.hoymiles_hit_energy_from_grid_today",
        ) or 0.0
        pv_to_load_power_w = _state_number(
            self.hass,
            "sensor.hoymiles_hit_load_from_pv_power",
        ) or 0.0
        pv_total_power_w = _state_number(
            self.hass,
            "sensor.hoymiles_hit_overview_pv_total_power",
        ) or 0.0
        load_power_w = _state_number(
            self.hass,
            "sensor.hoymiles_actual_load_power",
        )
        if load_power_w is None:
            phase_loads = tuple(
                _state_number(self.hass, entity_id)
                for entity_id in (
                    "sensor.hoymiles_hit_load_power_l1n",
                    "sensor.hoymiles_hit_load_power_l2n",
                    "sensor.hoymiles_hit_load_power_l3n",
                )
            )
            if all(value is not None for value in phase_loads):
                load_power_w = sum(
                    max(value or 0.0, 0.0) for value in phase_loads
                )
            else:
                load_power_w = _state_number(
                    self.hass,
                    "sensor.hoymiles_hit_overview_load_active_power",
                )
        load_power_w = max(load_power_w or 0.0, 0.0)
        # Rejestr 2180 potrafi zawierać straty przetwarzania. Dla udziału
        # autokonsumpcji ograniczamy go do rzeczywistego obciążenia domu.
        pv_to_load_power_w = min(
            max(pv_to_load_power_w, 0.0),
            load_power_w,
        )
        battery_age = _state_age_minutes(
            self.hass.states.get("sensor.hoymiles_hit_overview_battery_soc"),
            now,
        )
        rce_today_age = _state_age_minutes(today_rows_state, now)
        rce_tomorrow_age = _state_age_minutes(tomorrow_rows_state, now)
        forecast_today_age = _state_age_minutes(today_forecast_state, now)
        forecast_tomorrow_age = _state_age_minutes(
            tomorrow_forecast_state,
            now,
        )
        quality_issues: list[str] = []
        quality_score = 100
        if battery_age is None or battery_age > 10:
            quality_score -= 25
            quality_issues.append("battery_soc_stale")
        if forecast_today_age is None or forecast_today_age > 360:
            quality_score -= 15
            quality_issues.append("forecast_today_stale")
        if forecast_tomorrow_age is None or forecast_tomorrow_age > 720:
            quality_score -= 15
            quality_issues.append("forecast_tomorrow_stale")
        if rce_today_age is None or rce_today_age > 24 * 60:
            quality_score -= 20
            quality_issues.append("rce_today_stale")
        if not tomorrow_rows_complete:
            quality_score -= 10
            quality_issues.append("rce_tomorrow_pending")
        if not uncertainty_available:
            quality_score -= 10
            quality_issues.append("solcast_uncertainty_unavailable")
        if self._forecast_accuracy_days < 2:
            quality_score -= 5
            quality_issues.append("forecast_history_short")
        if profile_history.daily_history_days < 4:
            quality_score -= 10
            quality_issues.append("load_history_short")
        quality_score = max(quality_score, 0)
        quality_level = (
            "high"
            if quality_score >= 80
            else "medium"
            if quality_score >= 55
            else "low"
        )
        metadata.update(
            {
                "forecast_today_raw_kwh": round(forecast_today_raw, 2),
                "forecast_today_kwh": round(forecast_today, 2),
                "forecast_remaining_today_kwh": round(remaining_today, 2),
                "forecast_remaining_today_raw_kwh": round(
                    remaining_today_raw,
                    2,
                ),
                "forecast_tomorrow_raw_kwh": round(
                    forecast_tomorrow_raw,
                    2,
                ),
                "forecast_tomorrow_kwh": round(forecast_tomorrow, 2),
                "forecast_day3_kwh": (
                    round(day3_conservative, 2)
                    if day3_conservative is not None
                    else None
                ),
                "forecast_today_p10_kwh": (
                    round(today_p10_raw, 2)
                    if today_p10_raw is not None
                    else None
                ),
                "forecast_today_p90_kwh": (
                    round(today_p90_raw, 2)
                    if today_p90_raw is not None
                    else None
                ),
                "forecast_tomorrow_p10_kwh": (
                    round(tomorrow_p10_raw, 2)
                    if tomorrow_p10_raw is not None
                    else None
                ),
                "forecast_tomorrow_p90_kwh": (
                    round(tomorrow_p90_raw, 2)
                    if tomorrow_p90_raw is not None
                    else None
                ),
                "forecast_accuracy_factor": round(
                    self._forecast_accuracy_factor,
                    4,
                ),
                "forecast_accuracy_uncertainty": round(
                    self._forecast_accuracy_uncertainty,
                    4,
                ),
                "forecast_accuracy_history_days": (
                    self._forecast_accuracy_days
                ),
                "forecast_accuracy_source": self._forecast_accuracy_source,
                "forecast_today_effective_factor": round(
                    today_forecast_factor,
                    4,
                ),
                "forecast_live_ratio": (
                    round(live_forecast_ratio, 4)
                    if live_forecast_ratio is not None
                    else None
                ),
                "forecast_live_confidence": round(
                    live_forecast_confidence,
                    4,
                ),
                "forecast_live_expected_elapsed_kwh": (
                    round(expected_elapsed_raw, 2)
                    if expected_elapsed_raw is not None
                    else None
                ),
                "forecast_live_actual_elapsed_kwh": round(
                    actual_pv_today,
                    2,
                ),
                "forecast_uncertainty_available": uncertainty_available,
                "forecast_uncertainty_risk_weight": round(risk_weight, 4),
                "forecast_uncertainty_spread_ratio": round(
                    uncertainty_spread_ratio,
                    4,
                ),
                "forecast_conservative_horizon_kwh": round(
                    sum(conservative_pv_by_slot.values()),
                    2,
                ),
                "forecast_expected_horizon_kwh": round(
                    sum(pv_by_slot.values()),
                    2,
                ),
                "data_quality_score": quality_score,
                "data_quality_level": quality_level,
                "data_quality_issues": quality_issues,
                "battery_soc_age_minutes": (
                    round(battery_age, 1) if battery_age is not None else None
                ),
                "rce_today_age_minutes": (
                    round(rce_today_age, 1)
                    if rce_today_age is not None
                    else None
                ),
                "rce_tomorrow_age_minutes": (
                    round(rce_tomorrow_age, 1)
                    if rce_tomorrow_age is not None
                    else None
                ),
                "forecast_today_age_minutes": (
                    round(forecast_today_age, 1)
                    if forecast_today_age is not None
                    else None
                ),
                "forecast_tomorrow_age_minutes": (
                    round(forecast_tomorrow_age, 1)
                    if forecast_tomorrow_age is not None
                    else None
                ),
                "gcf_enabled": gcf_enabled,
                "gcf_export_limit_percent": gcf_limit_percent,
                "gcf_export_power_cap_kw": (
                    round(export_power_cap_kw, 2)
                    if export_power_cap_kw is not None
                    else None
                ),
                "effective_export_power_source": effective_export_source,
                "avoided_import_price_pln_kwh": round(
                    avoided_import_price,
                    4,
                ),
                "battery_wear_cost_pln_kwh": round(
                    battery_wear_cost,
                    4,
                ),
                "charge_efficiency_percent": round(charge_efficiency, 1),
                "house_discharge_efficiency_percent": round(
                    discharge_efficiency,
                    1,
                ),
                "average_load_4d_kwh": round(average_load, 2),
                "average_night_load_4d_kwh": (
                    round(night_load, 2) if night_load is not None else None
                ),
                "pv_to_load_today_kwh": round(pv_to_load_today, 2),
                "battery_to_load_today_kwh": round(
                    battery_to_load_today,
                    2,
                ),
                "grid_to_load_today_kwh": round(grid_to_load_today, 2),
                "pv_to_load_power_kw": round(
                    max(pv_to_load_power_w, 0.0) / 1000.0,
                    3,
                ),
                "pv_self_consumption_share_percent": round(
                    min(
                        max(pv_to_load_power_w, 0.0)
                        / max(pv_total_power_w, 0.001)
                        * 100.0,
                        100.0,
                    ),
                    1,
                ),
                "home_pv_coverage_percent": round(
                    min(
                        max(pv_to_load_power_w, 0.0)
                        / max(load_power_w, 0.001)
                        * 100.0,
                        100.0,
                    ),
                    1,
                ),
                "inverter_count": inverter_count,
                "inverter_power_each_kw": rated_power,
                "market_slots": len(price_slots),
                "night_window": (
                    f"{night_start // 60:02d}:{night_start % 60:02d}"
                    f"–{night_end // 60:02d}:{night_end % 60:02d}"
                ),
            }
        )
        return (
            OptimizerInput(
                now=now,
                price_slots=price_slots,
                pv_by_slot_kwh=pv_by_slot,
                battery_capacity_kwh=required[
                    "sensor.hoymiles_hit_battery_capacity"
                ],
                battery_soc_percent=required[
                    "sensor.hoymiles_hit_overview_battery_soc"
                ],
                outage_reserve_soc_percent=required[
                    "number.hoymiles_hit_self_use_soc"
                ],
                safety_margin_soc_percent=required[
                    "input_number.hoymiles_rce_soc_safety_margin"
                ],
                manual_minimum_soc_percent=required[
                    "number.hoymiles_hit_force_discharge_soc"
                ],
                dynamic_reserve_enabled=self.hass.states.is_state(
                    "input_boolean.hoymiles_rce_dynamic_soc_enabled",
                    "on",
                ),
                average_daily_load_kwh=average_load,
                average_night_load_kwh=night_load,
                night_start_minute=night_start,
                night_end_minute=night_end,
                inverter_power_kw=rated_power,
                inverter_count=inverter_count,
                discharge_power_percent=required[
                    "input_number.hoymiles_rce_requested_discharge_power"
                ],
                export_efficiency_percent=required[
                    "input_number.hoymiles_rce_export_efficiency"
                ],
                bms_max_discharge_current_a=_state_number(
                    self.hass,
                    "sensor.hoymiles_hit_maximum_discharge_current",
                ),
                battery_voltage_v=_state_number(
                    self.hass,
                    "sensor.hoymiles_hit_battery_voltage_bms",
                ),
                actual_day_load_today_kwh=(
                    self._load_history.current_day_energy_kwh
                ),
                pv_to_load_power_kw=max(pv_to_load_power_w, 0.0) / 1000.0,
                load_profile_30m_kwh=profile_history.average_profile_kwh,
                weekday_load_profile_30m_kwh=(
                    profile_history.weekday_profile_kwh
                ),
                weekend_load_profile_30m_kwh=(
                    profile_history.weekend_profile_kwh
                ),
                conservative_pv_by_slot_kwh=conservative_pv_by_slot,
                forecast_confidence_percent=forecast_confidence,
                export_power_cap_kw=export_power_cap_kw,
                effective_export_power_kw=effective_export_power_kw,
                avoided_import_price_pln_kwh=avoided_import_price,
                battery_wear_cost_pln_kwh=battery_wear_cost,
                day3_pv_forecast_kwh=day3_conservative,
                charge_efficiency_percent=charge_efficiency,
                house_discharge_efficiency_percent=discharge_efficiency,
            ),
            metadata,
        )

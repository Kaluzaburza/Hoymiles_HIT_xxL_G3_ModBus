"""Home Assistant sensor exposing the optimized two-day RCE plan."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .models import RuntimeData
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
    "sensor.hoymiles_hit_load_energy_use_today",
    "sensor.hoymiles_load_average_4_days",
    "sensor.hoymiles_night_load_average_4_days",
    "number.hoymiles_hit_self_use_soc",
    "number.hoymiles_hit_force_discharge_soc",
    "number.hoymiles_hit_maximum_discharge_power",
    "input_boolean.hoymiles_rce_dynamic_soc_enabled",
    "input_boolean.hoymiles_sale_block_enabled",
    "input_datetime.hoymiles_sale_block_start",
    "input_datetime.hoymiles_sale_block_end",
    "input_number.hoymiles_rce_price_threshold",
    "input_number.hoymiles_rce_soc_safety_margin",
    "input_number.hoymiles_rce_export_efficiency",
    "input_number.hoymiles_rce_fallback_daily_load",
    "input_select.hoymiles_rce_inverter_rated_power",
    "input_text.hoymiles_solcast_forecast_today_entity",
    "input_text.hoymiles_solcast_forecast_tomorrow_entity",
    "sun.sun",
    *TODAY_FORECAST_CANDIDATES,
    *TOMORROW_FORECAST_CANDIDATES,
    *REMAINING_TODAY_CANDIDATES,
}

STATUS_TEXT = {
    "pl": {
        "ready": "Gotowa — plan zoptymalizowany",
        "waiting_for_price": "Oczekiwanie — brak opłacalnego okna",
        "home_protected": "Zasilanie domu zabezpieczone — brak energii na sprzedaż",
        "home_energy_shortage": "Za mało energii na potrzeby domu — sprzedaż zablokowana",
        "missing_data": "Brak wymaganych danych — sprzedaż zablokowana",
        "optimizer_error": "Błąd obliczeń — sprzedaż zablokowana",
    },
    "en": {
        "ready": "Ready — optimized plan",
        "waiting_for_price": "Waiting — no profitable window",
        "home_protected": "Home supply protected — no energy available for export",
        "home_energy_shortage": "Insufficient home energy — export blocked",
        "missing_data": "Required data missing — export blocked",
        "optimizer_error": "Calculation error — export blocked",
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


def _detailed_pv_map(
    state: State | None,
    target_date: date,
    target_kwh: float,
    timezone: ZoneInfo,
    now_slot: datetime,
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
            and code in {"ready", "waiting_for_price", "home_protected"}
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
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _async_input_changed(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Recalculate after a source state changes."""
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _async_timer(self, now: datetime) -> None:
        """Refresh the active slot and rolling forecast every minute."""
        self._recalculate()
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
                "protected_home_energy_kwh": round(
                    result.protected_home_energy_kwh,
                    2,
                ),
                "available_energy_now_kwh": round(
                    result.available_energy_now_kwh,
                    2,
                ),
                "planned_export_kwh": round(result.planned_export_kwh, 2),
                "natural_pv_export_kwh": round(result.natural_export_kwh, 2),
                "expected_total_export_kwh": round(result.total_export_kwh, 2),
                "estimated_revenue_pln": round(result.total_revenue_pln, 2),
                "ending_battery_kwh": round(result.ending_battery_kwh, 2),
                "system_power_kw": round(result.system_power_kw, 2),
                "maximum_export_power_kw": round(
                    result.maximum_export_power_kw,
                    2,
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
            "number.hoymiles_hit_maximum_discharge_power": _state_number(
                self.hass,
                "number.hoymiles_hit_maximum_discharge_power",
            ),
            "input_number.hoymiles_rce_price_threshold": _state_number(
                self.hass,
                "input_number.hoymiles_rce_price_threshold",
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
        if today_forecast_state is None:
            required["Solcast Forecast Today"] = None
        if tomorrow_forecast_state is None:
            required["Solcast Forecast Tomorrow"] = None

        history_load = _state_number(
            self.hass,
            "sensor.hoymiles_load_average_4_days",
        )
        fallback_load = _state_number(
            self.hass,
            "input_number.hoymiles_rce_fallback_daily_load",
        )
        average_load = history_load if history_load is not None else fallback_load
        if average_load is None:
            required["sensor.hoymiles_load_average_4_days"] = None

        missing = sorted(
            entity_id for entity_id, value in required.items() if value is None
        )
        metadata: dict[str, Any] = {
            "missing_entities": missing,
            "forecast_today_entity": today_entity or "none",
            "forecast_tomorrow_entity": tomorrow_entity or "none",
            "forecast_remaining_today_entity": remaining_entity or "fallback",
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
            "load_model_source": (
                "history_4_days" if history_load is not None else "user_fallback"
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

        forecast_today = float(today_forecast_state.state)
        forecast_tomorrow = float(tomorrow_forecast_state.state)
        actual_pv_today = _state_number(
            self.hass,
            "sensor.hoymiles_hit_pv_total_energy_today",
        ) or 0.0
        remaining_today = (
            float(remaining_state.state)
            if remaining_state is not None
            else max(forecast_today - actual_pv_today, 0.0)
        )
        sunrise_minute = rising.hour * 60 + rising.minute
        sunset_minute = setting.hour * 60 + setting.minute
        night_start = (sunset_minute - 90) % (24 * 60)
        night_end = (sunrise_minute + 90) % (24 * 60)

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
        pv_by_slot = dict(pv_today)
        for start, energy in pv_tomorrow.items():
            pv_by_slot[start] = pv_by_slot.get(start, 0.0) + energy

        inverter_count_raw = _state_number(
            self.hass,
            "sensor.hoymiles_hit_number_of_machines_master_and_slave",
        )
        inverter_count = min(
            max(round(inverter_count_raw or 1.0), 1),
            10,
        )
        night_load = _state_number(
            self.hass,
            "sensor.hoymiles_night_load_average_4_days",
        )
        metadata.update(
            {
                "forecast_today_kwh": round(forecast_today, 2),
                "forecast_remaining_today_kwh": round(remaining_today, 2),
                "forecast_tomorrow_kwh": round(forecast_tomorrow, 2),
                "average_load_4d_kwh": round(average_load, 2),
                "average_night_load_4d_kwh": (
                    round(night_load, 2) if night_load is not None else None
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
                minimum_price_pln_kwh=required[
                    "input_number.hoymiles_rce_price_threshold"
                ],
                inverter_power_kw=rated_power,
                inverter_count=inverter_count,
                discharge_power_percent=required[
                    "number.hoymiles_hit_maximum_discharge_power"
                ],
                export_efficiency_percent=required[
                    "input_number.hoymiles_rce_export_efficiency"
                ],
            ),
            metadata,
        )

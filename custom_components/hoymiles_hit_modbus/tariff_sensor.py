"""Home Assistant sensor exposing the automatic tariff charging plan."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import partial
import logging
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder import history as recorder_history
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
from .models import RuntimeData
from .rce_sensor import (
    REMAINING_TODAY_CANDIDATES,
    TODAY_FORECAST_CANDIDATES,
    TOMORROW_FORECAST_CANDIDATES,
    _detailed_pv_map,
    _fallback_pv_map,
    _first_numeric_state,
    _helper_minutes,
    _select_number,
    _state_number,
    _state_text,
)
from .tariff_optimizer import (
    TariffOptimizerInput,
    TariffSchedule,
    adaptive_forecast_factor,
    floor_half_hour,
    is_polish_public_holiday,
    optimize_tariff_charging,
)
from .tariff_profiles import (
    MANUAL_OPERATOR,
    PROFILE_YEAR,
    SUPPORTED_OPERATORS,
    get_tariff_profile,
    profile_summary,
)


_LOGGER = logging.getLogger(__name__)

WATCHED_TARIFF_ENTITIES = {
    "sensor.hoymiles_hit_rce_optimized_plan",
    "sensor.hoymiles_hit_battery_capacity",
    "sensor.hoymiles_hit_overview_battery_soc",
    "sensor.hoymiles_hit_battery_voltage_bms",
    "sensor.hoymiles_hit_maximum_charge_current",
    "sensor.hoymiles_hit_maximum_discharge_current",
    "sensor.hoymiles_hit_number_of_machines_master_and_slave",
    "sensor.hoymiles_hit_pv_total_energy_today",
    "number.hoymiles_hit_self_use_soc",
    "input_boolean.hoymiles_tariff_charge_enabled",
    "input_boolean.hoymiles_tariff_weekend_low_price",
    "input_boolean.hoymiles_tariff_polish_holidays_low_price",
    "input_select.hoymiles_tariff_operator",
    "input_select.hoymiles_tariff_type",
    "input_select.hoymiles_rce_inverter_rated_power",
    "input_number.hoymiles_tariff_g11_price",
    "input_number.hoymiles_tariff_low_price",
    "input_number.hoymiles_tariff_medium_price",
    "input_number.hoymiles_tariff_peak_price",
    "input_number.hoymiles_tariff_requested_charge_power",
    "input_number.hoymiles_tariff_charge_efficiency",
    "input_number.hoymiles_tariff_discharge_efficiency",
    "input_number.hoymiles_tariff_minimum_saving",
    "input_number.hoymiles_tariff_soc_safety_margin",
    "input_number.hoymiles_tariff_maximum_soc",
    "input_datetime.hoymiles_tariff_cheap_1_start",
    "input_datetime.hoymiles_tariff_cheap_1_end",
    "input_datetime.hoymiles_tariff_cheap_2_start",
    "input_datetime.hoymiles_tariff_cheap_2_end",
    "input_datetime.hoymiles_tariff_medium_start",
    "input_datetime.hoymiles_tariff_medium_end",
    "input_text.hoymiles_solcast_forecast_today_entity",
    "input_text.hoymiles_solcast_forecast_tomorrow_entity",
    "sun.sun",
    *TODAY_FORECAST_CANDIDATES,
    *TOMORROW_FORECAST_CANDIDATES,
    *REMAINING_TODAY_CANDIDATES,
}

STATUS_TEXT = {
    "pl": {
        "ready": "Gotowa — zaplanowano tanie ładowanie",
        "no_charge_needed": "Brak potrzeby doładowania — PV i bateria wystarczą",
        "no_discount_window": "G11 — brak tańszej strefy do wykorzystania",
        "no_cheap_window": "Brak taniego okna przed prognozowanym deficytem",
        "shortage_in_low_period": (
            "Deficyt przypada w taniej strefie — bezpośredni pobór bez strat baterii"
        ),
        "insufficient_cheap_window": "Tanie okna nie pokryją całego deficytu",
        "missing_data": "Brak wymaganych danych — ładowanie zablokowane",
        "optimizer_error": "Błąd obliczeń — ładowanie zablokowane",
        "unsupported_profile": "Ta grupa nie jest dostępna u wybranego operatora",
        "expired_profile": "Cennik wygasł — automatyczne ładowanie zablokowane",
    },
    "en": {
        "ready": "Ready — low-cost charging planned",
        "no_charge_needed": "No charging needed — PV and battery are sufficient",
        "no_discount_window": "G11 — no lower-cost period available",
        "no_cheap_window": "No low-cost period before the forecast shortage",
        "shortage_in_low_period": (
            "Shortage occurs in the low-cost period — direct import avoids battery losses"
        ),
        "insufficient_cheap_window": "Low-cost periods cannot cover the full shortage",
        "missing_data": "Required data missing — charging blocked",
        "optimizer_error": "Calculation error — charging blocked",
        "unsupported_profile": "This tariff group is unavailable for the selected DSO",
        "expired_profile": "Tariff prices expired — automatic charging blocked",
    },
}


def _state_attribute_number(
    state: State | None,
    attribute: str,
) -> float | None:
    if state is None:
        return None
    try:
        return float(state.attributes[attribute])
    except (KeyError, TypeError, ValueError):
        return None


def _state_attribute_profile(
    state: State | None,
    attribute: str,
) -> tuple[float, ...]:
    """Return one validated 48-slot recorder profile from an entity attribute."""
    if state is None:
        return ()
    raw = state.attributes.get(attribute)
    if not isinstance(raw, (list, tuple)) or len(raw) != 48:
        return ()
    try:
        values = tuple(max(float(value), 0.0) for value in raw)
    except (TypeError, ValueError):
        return ()
    return values if sum(values) > 0 else ()


def _detailed_pv_expected_elapsed_kwh(
    state: State | None,
    target_date: date,
    timezone: ZoneInfo,
    now: datetime,
) -> float | None:
    """Return raw Solcast energy expected from midnight until ``now``.

    The integration exposes half-hour power estimates.  The current interval
    is counted proportionally so a recalculation shortly after sunrise does
    not compare a few minutes of real production with a complete 30-minute
    forecast block.
    """
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
        if not isinstance(item, dict):
            continue
        raw_start = item.get("period_start") or item.get("period_start_local")
        if not isinstance(raw_start, str):
            continue
        start = dt_util.parse_datetime(raw_start)
        if start is None:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone)
        start = start.astimezone(timezone)
        if start.date() != target_date or start >= now:
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
        elapsed_fraction = min(
            max((now - start).total_seconds() / (30 * 60), 0.0),
            1.0,
        )
        expected += power_kw * 0.5 * elapsed_fraction
        found = True
    return expected if found else None


class HoymilesTariffOptimizerSensor(SensorEntity):
    """Calculate a home-first, two-day low-tariff charging plan."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "tariff_charge_plan"
    _attr_icon = "mdi:battery-clock-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: RuntimeData,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_tariff_charge_plan"
        self._attributes: dict[str, Any] = {
            "status_code": "missing_data",
            "missing_entities": [],
            "planned_slots": [],
        }
        self._forecast_accuracy_factor = 0.90
        self._forecast_accuracy_days = 0
        self._forecast_accuracy_source = "automatic_conservative_fallback"
        self._forecast_refresh_running = False
        self._recalculate_cancel = None

    @property
    def suggested_object_id(self) -> str:
        return "hoymiles_hit_tariff_charge_plan"

    @property
    def device_info(self) -> DeviceInfo:
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
        language = "pl" if self.hass.config.language.startswith("pl") else "en"
        code = str(self._attributes.get("status_code", "missing_data"))
        return STATUS_TEXT[language].get(
            code,
            STATUS_TEXT[language]["optimizer_error"],
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                sorted(WATCHED_TARIFF_ENTITIES),
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
                self._async_forecast_accuracy_timer,
                timedelta(hours=1),
            )
        )
        await self._async_refresh_forecast_accuracy()
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _async_input_changed(self, event: Event[EventStateChangedData]) -> None:
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
        if self._recalculate_cancel is not None:
            self._recalculate_cancel()
            self._recalculate_cancel = None
        self._recalculate_and_write()

    async def _async_forecast_accuracy_timer(self, now: datetime) -> None:
        """Refresh the automatic Solcast bias correction once per hour."""
        await self._async_refresh_forecast_accuracy()
        self._recalculate_and_write()

    def _recalculate_and_write(self) -> None:
        """Write only when the plan or its diagnostics actually changed."""
        previous_state = self.native_value
        previous_attributes = self._attributes
        self._recalculate()
        if previous_state != self.native_value or previous_attributes != self._attributes:
            self.async_write_ha_state()

    async def _async_refresh_forecast_accuracy(self) -> None:
        """Learn a conservative PV factor from complete local days."""
        if self._forecast_refresh_running:
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
            timezone = ZoneInfo(self.hass.config.time_zone)
            now = dt_util.now().astimezone(timezone)
            start = now - timedelta(days=7)
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
            raw = await get_recorder_instance(self.hass).async_add_executor_job(query)
            forecast_by_day: dict[Any, list[float]] = {}
            actual_by_day: dict[Any, list[float]] = {}
            for entity_id, destination in (
                (forecast_entity, forecast_by_day),
                (actual_entity, actual_by_day),
            ):
                for item in raw.get(entity_id, []):
                    updated = getattr(item, "last_updated", None)
                    state_value = getattr(item, "state", None)
                    if updated is None or state_value is None:
                        continue
                    try:
                        numeric = max(float(state_value), 0.0)
                    except (TypeError, ValueError):
                        continue
                    local_day = updated.astimezone(timezone).date()
                    if local_day >= now.date():
                        continue
                    destination.setdefault(local_day, []).append(numeric)

            ratios: list[float] = []
            for day in sorted(set(forecast_by_day) & set(actual_by_day))[-5:]:
                forecasts = [value for value in forecast_by_day[day] if value > 0.5]
                actuals = actual_by_day[day]
                if not forecasts or not actuals:
                    continue
                forecast = median(forecasts)
                actual = max(actuals)
                if actual <= 0.5:
                    continue
                ratios.append(min(max(actual / forecast, 0.65), 1.10))
            if ratios:
                # Never increase a forecast automatically; optimism could cause
                # the home reserve to be undersized.  Underproduction is learned.
                self._forecast_accuracy_factor = min(median(ratios), 1.0)
                self._forecast_accuracy_days = len(ratios)
                self._forecast_accuracy_source = "recorder_actual_vs_solcast"
        except Exception:  # noqa: BLE001 - retain the conservative safe fallback
            _LOGGER.exception("Cannot learn Solcast forecast accuracy")
        finally:
            self._forecast_refresh_running = False

    def _recalculate(self) -> None:
        try:
            settings, metadata = self._optimizer_input()
            if settings is None:
                status_code = str(metadata.pop("_status_code", "missing_data"))
                self._attributes = {
                    "status_code": status_code,
                    "planned_slots": [],
                    **metadata,
                }
                return
            result = optimize_tariff_charging(settings)
            self._attributes = {
                "status_code": result.status_code,
                "missing_entities": [],
                "planned_slots": [
                    {
                        "date": item.start.date().isoformat(),
                        "start": item.start.strftime("%H:%M"),
                        "end": (item.start + timedelta(minutes=30)).strftime("%H:%M"),
                        "zone": item.zone,
                        "price": round(item.price_pln_kwh, 4),
                        "action": item.action,
                        "grid_import_kwh": round(item.grid_import_kwh, 3),
                        "stored_energy_kwh": round(item.stored_energy_kwh, 3),
                        "direct_load_kwh": round(item.direct_load_kwh, 3),
                        "target_soc_percent": round(item.target_soc_percent, 1),
                    }
                    for item in result.planned_charges
                ],
                "current_slot_planned": result.current_slot_planned,
                "current_action": result.current_action,
                "current_slot_end": (
                    result.current_slot_end.isoformat()
                    if result.current_slot_end is not None
                    else None
                ),
                "current_price_pln_kwh": round(result.current_price_pln_kwh, 4),
                "current_zone": result.current_zone,
                "next_charge_start": (
                    result.next_charge_start.isoformat()
                    if result.next_charge_start is not None
                    else None
                ),
                "target_soc_percent": round(result.target_soc_percent, 1),
                "baseline_shortage_kwh": round(result.baseline_shortage_kwh, 2),
                "remaining_shortage_kwh": round(result.remaining_shortage_kwh, 2),
                "planned_grid_import_kwh": round(result.planned_grid_import_kwh, 2),
                "planned_stored_energy_kwh": round(
                    result.planned_stored_energy_kwh,
                    2,
                ),
                "planned_direct_load_kwh": round(
                    result.planned_direct_load_kwh,
                    2,
                ),
                "planned_cost_pln": round(result.planned_cost_pln, 2),
                "baseline_grid_cost_pln": round(
                    result.baseline_grid_cost_pln,
                    2,
                ),
                "optimized_grid_cost_pln": round(
                    result.optimized_grid_cost_pln,
                    2,
                ),
                "automation_savings_pln": round(
                    result.automation_savings_pln,
                    2,
                ),
                "baseline_grid_import_kwh": round(
                    result.baseline_grid_import_kwh,
                    2,
                ),
                "optimized_grid_import_kwh": round(
                    result.optimized_grid_import_kwh,
                    2,
                ),
                "g11_reference_cost_pln": round(result.g11_reference_cost_pln, 2),
                "estimated_savings_pln": round(result.estimated_savings_pln, 2),
                "ending_battery_kwh": round(result.ending_battery_kwh, 2),
                "ending_battery_soc_percent": round(
                    result.ending_battery_soc_percent,
                    1,
                ),
                "effective_charge_power_kw": round(result.charge_power_kw, 2),
                **metadata,
            }
        except Exception:  # noqa: BLE001 - automation must fail closed
            _LOGGER.exception("Cannot calculate the tariff charging plan")
            self._attributes = {
                "status_code": "optimizer_error",
                "missing_entities": [],
                "planned_slots": [],
                "current_slot_planned": False,
                "current_action": "none",
                "current_slot_end": None,
            }

    def _optimizer_input(
        self,
    ) -> tuple[TariffOptimizerInput | None, dict[str, Any]]:
        timezone = ZoneInfo(self.hass.config.time_zone)
        now = dt_util.now().astimezone(timezone)
        now_slot = floor_half_hour(now)
        rce_state = self.hass.states.get("sensor.hoymiles_hit_rce_optimized_plan")

        required: dict[str, float | None] = {
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
            "input_number.hoymiles_tariff_soc_safety_margin": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_soc_safety_margin",
            ),
            "input_number.hoymiles_tariff_maximum_soc": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_maximum_soc",
            ),
            "input_number.hoymiles_tariff_requested_charge_power": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_requested_charge_power",
            ),
            "input_number.hoymiles_tariff_charge_efficiency": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_charge_efficiency",
            ),
            "input_number.hoymiles_tariff_discharge_efficiency": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_discharge_efficiency",
            ),
            "input_number.hoymiles_tariff_minimum_saving": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_minimum_saving",
            ),
            "input_number.hoymiles_tariff_g11_price": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_g11_price",
            ),
            "input_number.hoymiles_tariff_low_price": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_low_price",
            ),
            "input_number.hoymiles_tariff_medium_price": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_medium_price",
            ),
            "input_number.hoymiles_tariff_peak_price": _state_number(
                self.hass,
                "input_number.hoymiles_tariff_peak_price",
            ),
        }
        daily_load = _state_attribute_number(
            rce_state,
            "selected_average_daily_load_kwh",
        )
        night_load = _state_attribute_number(
            rce_state,
            "average_night_load_4d_kwh",
        )
        if daily_load is None:
            daily_load = _state_number(
                self.hass,
                "input_number.hoymiles_rce_fallback_daily_load",
            )
        if daily_load is None:
            required["sensor.hoymiles_load_average_4_days"] = None
        average_load_profile = _state_attribute_profile(
            rce_state,
            "recorder_load_profile_30m_kwh",
        )
        weekday_load_profile = _state_attribute_profile(
            rce_state,
            "recorder_load_weekday_profile_30m_kwh",
        )
        weekend_load_profile = _state_attribute_profile(
            rce_state,
            "recorder_load_weekend_profile_30m_kwh",
        )

        rated_power = _select_number(
            self.hass,
            "input_select.hoymiles_rce_inverter_rated_power",
        )
        if rated_power is None:
            required["input_select.hoymiles_rce_inverter_rated_power"] = None

        window_entities = (
            "input_datetime.hoymiles_tariff_cheap_1_start",
            "input_datetime.hoymiles_tariff_cheap_1_end",
            "input_datetime.hoymiles_tariff_cheap_2_start",
            "input_datetime.hoymiles_tariff_cheap_2_end",
            "input_datetime.hoymiles_tariff_medium_start",
            "input_datetime.hoymiles_tariff_medium_end",
        )
        window_values = {
            entity_id: _helper_minutes(self.hass, entity_id)
            for entity_id in window_entities
        }
        required.update(window_values)

        tariff_type = _state_text(self.hass, "input_select.hoymiles_tariff_type")
        if tariff_type not in {"G11", "G12", "G12w", "G13"}:
            required["input_select.hoymiles_tariff_type"] = None
        operator = _state_text(
            self.hass,
            "input_select.hoymiles_tariff_operator",
        )
        if operator not in {*SUPPORTED_OPERATORS, MANUAL_OPERATOR}:
            required["input_select.hoymiles_tariff_operator"] = None
            operator = MANUAL_OPERATOR
        profile = (
            get_tariff_profile(operator, tariff_type)
            if operator != MANUAL_OPERATOR and tariff_type is not None
            else None
        )
        unsupported_profile = (
            operator != MANUAL_OPERATOR
            and tariff_type in {"G11", "G12", "G12w", "G13"}
            and profile is None
        )

        today_configured = _state_text(
            self.hass,
            "input_text.hoymiles_solcast_forecast_today_entity",
        )
        tomorrow_configured = _state_text(
            self.hass,
            "input_text.hoymiles_solcast_forecast_tomorrow_entity",
        )
        today_entity, today_state = _first_numeric_state(
            self.hass,
            TODAY_FORECAST_CANDIDATES,
            today_configured,
        )
        tomorrow_entity, tomorrow_state = _first_numeric_state(
            self.hass,
            TOMORROW_FORECAST_CANDIDATES,
            tomorrow_configured,
        )
        remaining_entity, remaining_state = _first_numeric_state(
            self.hass,
            REMAINING_TODAY_CANDIDATES,
        )
        if today_state is None:
            required["Solcast Forecast Today"] = None
        if tomorrow_state is None:
            required["Solcast Forecast Tomorrow"] = None

        sunrise_today = get_astral_event_date(self.hass, "sunrise", now.date())
        sunset_today = get_astral_event_date(self.hass, "sunset", now.date())
        sunrise_tomorrow = get_astral_event_date(
            self.hass,
            "sunrise",
            now.date() + timedelta(days=1),
        )
        sunset_tomorrow = get_astral_event_date(
            self.hass,
            "sunset",
            now.date() + timedelta(days=1),
        )
        if any(
            value is None
            for value in (
                sunrise_today,
                sunset_today,
                sunrise_tomorrow,
                sunset_tomorrow,
            )
        ):
            required["sun.sun"] = None

        missing = sorted(key for key, value in required.items() if value is None)
        metadata: dict[str, Any] = {
            "missing_entities": missing,
            "automatic_charge_enabled": self.hass.states.is_state(
                "input_boolean.hoymiles_tariff_charge_enabled",
                "on",
            ),
            "plan_is_preview": not self.hass.states.is_state(
                "input_boolean.hoymiles_tariff_charge_enabled",
                "on",
            ),
            "tariff_type": tariff_type or "none",
            "tariff_operator": operator,
            "forecast_today_entity": today_entity or "none",
            "forecast_tomorrow_entity": tomorrow_entity or "none",
            "forecast_remaining_today_entity": remaining_entity or "fallback",
            "average_daily_load_kwh": (
                round(daily_load, 2) if daily_load is not None else None
            ),
            "average_night_load_kwh": (
                round(night_load, 2) if night_load is not None else None
            ),
            "history_complete": (
                bool(rce_state.attributes.get("history_complete"))
                if rce_state is not None
                else False
            ),
            "load_profile_mode": (
                "recorder_30m_weekday_weekend"
                if weekday_load_profile or weekend_load_profile
                else (
                    "recorder_30m_average"
                    if average_load_profile
                    else "flat_day_night_fallback"
                )
            ),
            "price_model": (
                "manual marginal all-in prices; fixed monthly fees excluded"
                if operator == MANUAL_OPERATOR
                else "official 2026 regional profile; fixed monthly fees excluded"
            ),
            "default_price_reference": (
                "manual user invoice"
                if operator == MANUAL_OPERATOR
                else "2026 incumbent supplier and selected DSO tariffs"
            ),
            "tariff_profile_expired": (
                operator != MANUAL_OPERATOR and now.year != PROFILE_YEAR
            ),
            "forecast_accuracy_factor": round(
                self._forecast_accuracy_factor,
                3,
            ),
            "forecast_accuracy_history_days": self._forecast_accuracy_days,
            "forecast_accuracy_source": self._forecast_accuracy_source,
        }
        if profile is not None:
            metadata.update(
                profile_summary(
                    profile,
                    now,
                    is_public_holiday=is_polish_public_holiday(now.date()),
                )
            )
        elif unsupported_profile:
            metadata.update(
                {
                    "_status_code": "unsupported_profile",
                    "tariff_profile_supported": False,
                    "missing_entities": [],
                }
            )
            return None, metadata
        if metadata["tariff_profile_expired"]:
            metadata.update(
                {
                    "_status_code": "expired_profile",
                    "missing_entities": [],
                }
            )
            return None, metadata
        if missing:
            return None, metadata

        assert daily_load is not None
        assert rated_power is not None
        assert today_state is not None
        assert tomorrow_state is not None
        assert sunrise_today is not None
        assert sunset_today is not None
        assert sunrise_tomorrow is not None
        assert sunset_tomorrow is not None

        forecast_today = max(float(today_state.state), 0.0)
        forecast_tomorrow_raw = max(float(tomorrow_state.state), 0.0)
        actual_pv_today = _state_number(
            self.hass,
            "sensor.hoymiles_hit_pv_total_energy_today",
        ) or 0.0
        sunrise_today_local = sunrise_today.astimezone(timezone)
        sunset_today_local = sunset_today.astimezone(timezone)
        sunrise_tomorrow_local = sunrise_tomorrow.astimezone(timezone)
        sunset_tomorrow_local = sunset_tomorrow.astimezone(timezone)
        expected_elapsed_raw = _detailed_pv_expected_elapsed_kwh(
            today_state,
            now.date(),
            timezone,
            now,
        )
        live_adjustment_eligible = (
            now >= sunrise_today_local + timedelta(minutes=90)
            and now <= sunset_today_local + timedelta(minutes=30)
        )
        (
            today_forecast_factor,
            live_forecast_ratio,
            live_forecast_confidence,
        ) = adaptive_forecast_factor(
            self._forecast_accuracy_factor,
            actual_pv_today,
            expected_elapsed_raw,
            eligible=live_adjustment_eligible,
        )
        forecast_tomorrow = (
            forecast_tomorrow_raw * self._forecast_accuracy_factor
        )
        remaining_today_raw = (
            max(float(remaining_state.state), 0.0)
            if remaining_state is not None
            else max(forecast_today - actual_pv_today, 0.0)
        )
        remaining_today = remaining_today_raw * today_forecast_factor

        pv_today = _detailed_pv_map(
            today_state,
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
                sunrise_today_local.hour * 60 + sunrise_today_local.minute,
                sunset_today_local.hour * 60 + sunset_today_local.minute,
            )
        tomorrow_date = now.date() + timedelta(days=1)
        pv_tomorrow = _detailed_pv_map(
            tomorrow_state,
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
                sunrise_tomorrow_local.hour * 60 + sunrise_tomorrow_local.minute,
                sunset_tomorrow_local.hour * 60 + sunset_tomorrow_local.minute,
            )
        pv_by_slot = dict(pv_today)
        for start, energy in pv_tomorrow.items():
            pv_by_slot[start] = pv_by_slot.get(start, 0.0) + energy

        load_by_slot: dict[datetime, float] | None = None
        if average_load_profile or weekday_load_profile or weekend_load_profile:
            load_by_slot = {}
            horizon_end = datetime.combine(
                now.date() + timedelta(days=2),
                datetime.min.time(),
                tzinfo=timezone,
            )
            cursor = now_slot
            while cursor < horizon_end:
                category_profile = (
                    weekend_load_profile
                    if cursor.weekday() >= 5
                    else weekday_load_profile
                )
                # Keep the selected tariff profile intact.  Reusing the name
                # ``profile`` here replaced TariffProfile with a 48-slot load
                # tuple and broke every automatic (non-Manual) operator after
                # recorder history became available.
                slot_profile = category_profile or average_load_profile
                if slot_profile:
                    profile_total = sum(slot_profile)
                    scale = daily_load / profile_total if profile_total > 0 else 1.0
                    slot = cursor.hour * 2 + cursor.minute // 30
                    load_by_slot[cursor] = slot_profile[slot] * scale
                cursor += timedelta(minutes=30)

        inverter_count_raw = _state_number(
            self.hass,
            "sensor.hoymiles_hit_number_of_machines_master_and_slave",
        )
        inverter_count = min(max(round(inverter_count_raw or 1.0), 1), 10)
        system_power_kw = rated_power * inverter_count
        requested_percent = required[
            "input_number.hoymiles_tariff_requested_charge_power"
        ]
        assert requested_percent is not None
        requested_power_kw = system_power_kw * requested_percent / 100.0
        battery_voltage = _state_number(
            self.hass,
            "sensor.hoymiles_hit_battery_voltage_bms",
        )
        bms_current = _state_number(
            self.hass,
            "sensor.hoymiles_hit_maximum_charge_current",
        )
        bms_discharge_current = _state_number(
            self.hass,
            "sensor.hoymiles_hit_maximum_discharge_current",
        )
        bms_power_kw = (
            max(battery_voltage, 0.0) * max(bms_current, 0.0) / 1000.0
            if battery_voltage is not None and bms_current is not None
            else None
        )
        bms_discharge_power_kw = (
            max(battery_voltage, 0.0)
            * max(bms_discharge_current, 0.0)
            / 1000.0
            if battery_voltage is not None and bms_discharge_current is not None
            else None
        )
        # Maximum Charge Power is the complete AC budget used by Grid Charge.
        # The inverter supplies LOAD first and directs only the remainder to
        # the battery.  The BMS value therefore limits the battery branch, not
        # the complete grid input; combining them here would subtract LOAD
        # twice and systematically undercharge the storage.
        effective_power_kw = requested_power_kw

        cheap_windows = (
            (
                window_values["input_datetime.hoymiles_tariff_cheap_1_start"],
                window_values["input_datetime.hoymiles_tariff_cheap_1_end"],
            ),
            (
                window_values["input_datetime.hoymiles_tariff_cheap_2_start"],
                window_values["input_datetime.hoymiles_tariff_cheap_2_end"],
            ),
        )
        medium_windows = (
            (
                window_values["input_datetime.hoymiles_tariff_medium_start"],
                window_values["input_datetime.hoymiles_tariff_medium_end"],
            ),
        )
        assert all(
            value is not None
            for pair in (*cheap_windows, *medium_windows)
            for value in pair
        )

        self_use_reserve_soc = required["number.hoymiles_hit_self_use_soc"]
        safety_margin_soc = required[
            "input_number.hoymiles_tariff_soc_safety_margin"
        ]
        assert self_use_reserve_soc is not None
        assert safety_margin_soc is not None
        reserve_soc = min(
            max(self_use_reserve_soc + safety_margin_soc, 0.0),
            100.0,
        )
        night_start = (
            sunset_today_local.hour * 60 + sunset_today_local.minute - 90
        ) % (24 * 60)
        night_end = (
            sunrise_tomorrow_local.hour * 60 + sunrise_tomorrow_local.minute + 90
        ) % (24 * 60)
        metadata.update(
            {
                "forecast_remaining_today_kwh": round(remaining_today, 2),
                "forecast_tomorrow_kwh": round(forecast_tomorrow, 2),
                "forecast_remaining_today_raw_kwh": round(
                    remaining_today_raw,
                    2,
                ),
                "forecast_tomorrow_raw_kwh": round(
                    forecast_tomorrow_raw,
                    2,
                ),
                "forecast_today_effective_factor": round(
                    today_forecast_factor,
                    3,
                ),
                "forecast_live_expected_elapsed_kwh": (
                    round(expected_elapsed_raw, 2)
                    if expected_elapsed_raw is not None
                    else None
                ),
                "forecast_live_actual_elapsed_kwh": round(actual_pv_today, 2),
                "forecast_live_ratio": (
                    round(live_forecast_ratio, 3)
                    if live_forecast_ratio is not None
                    else None
                ),
                "forecast_live_confidence": round(
                    live_forecast_confidence,
                    3,
                ),
                "forecast_live_adjustment_active": (
                    today_forecast_factor
                    < self._forecast_accuracy_factor - 0.001
                ),
                "battery_capacity_kwh": round(
                    required["sensor.hoymiles_hit_battery_capacity"],
                    2,
                ),
                "battery_soc_percent": round(
                    required["sensor.hoymiles_hit_overview_battery_soc"],
                    1,
                ),
                "reserve_soc_percent": round(reserve_soc, 1),
                "self_use_reserve_soc_percent": round(
                    self_use_reserve_soc,
                    1,
                ),
                "safety_margin_soc_percentage_points": round(
                    safety_margin_soc,
                    1,
                ),
                "inverter_count": inverter_count,
                "inverter_power_each_kw": rated_power,
                "system_power_kw": round(system_power_kw, 2),
                "requested_charge_power_kw": round(requested_power_kw, 2),
                "bms_charge_power_limit_kw": (
                    round(bms_power_kw, 2) if bms_power_kw is not None else None
                ),
                "bms_discharge_power_limit_kw": (
                    round(bms_discharge_power_kw, 2)
                    if bms_discharge_power_kw is not None
                    else None
                ),
                "bms_limit_active": (
                    bms_power_kw is not None
                    and bms_power_kw + 0.05 < requested_power_kw
                ),
                "effective_charge_power_percent": round(
                    requested_percent,
                    1,
                ),
                "night_window": (
                    f"{night_start // 60:02d}:{night_start % 60:02d}–"
                    f"{night_end // 60:02d}:{night_end % 60:02d}"
                ),
            }
        )
        return (
            TariffOptimizerInput(
                now=now,
                pv_by_slot_kwh=pv_by_slot,
                battery_capacity_kwh=required[
                    "sensor.hoymiles_hit_battery_capacity"
                ],
                battery_soc_percent=required[
                    "sensor.hoymiles_hit_overview_battery_soc"
                ],
                reserve_soc_percent=reserve_soc,
                maximum_soc_percent=required[
                    "input_number.hoymiles_tariff_maximum_soc"
                ],
                average_daily_load_kwh=daily_load,
                average_night_load_kwh=night_load,
                night_start_minute=night_start,
                night_end_minute=night_end,
                charge_power_kw=effective_power_kw,
                battery_charge_power_kw=(
                    bms_power_kw
                    if bms_power_kw is not None
                    else system_power_kw
                ),
                battery_discharge_power_kw=(
                    bms_discharge_power_kw
                    if bms_discharge_power_kw is not None
                    else system_power_kw
                ),
                charge_efficiency_percent=required[
                    "input_number.hoymiles_tariff_charge_efficiency"
                ],
                discharge_efficiency_percent=required[
                    "input_number.hoymiles_tariff_discharge_efficiency"
                ],
                minimum_saving_pln_kwh=required[
                    "input_number.hoymiles_tariff_minimum_saving"
                ],
                schedule=TariffSchedule(
                    tariff_type=tariff_type,
                    g11_price_pln_kwh=(
                        profile.g11_price_pln_kwh
                        if profile is not None
                        else required["input_number.hoymiles_tariff_g11_price"]
                    ),
                    low_price_pln_kwh=(
                        profile.low_price_pln_kwh
                        if profile is not None
                        else required["input_number.hoymiles_tariff_low_price"]
                    ),
                    medium_price_pln_kwh=(
                        profile.medium_price_pln_kwh
                        if profile is not None
                        else required["input_number.hoymiles_tariff_medium_price"]
                    ),
                    peak_price_pln_kwh=(
                        profile.peak_price_pln_kwh
                        if profile is not None
                        else required["input_number.hoymiles_tariff_peak_price"]
                    ),
                    cheap_windows=cheap_windows,  # type: ignore[arg-type]
                    medium_windows=medium_windows,  # type: ignore[arg-type]
                    weekend_low_price=(
                        tariff_type in {"G12w", "G13"}
                        and self.hass.states.is_state(
                            "input_boolean.hoymiles_tariff_weekend_low_price",
                            "on",
                        )
                    ),
                    polish_holidays_low_price=self.hass.states.is_state(
                        "input_boolean.hoymiles_tariff_polish_holidays_low_price",
                        "on",
                    ),
                    operator=operator,
                ),
                load_by_slot_kwh=load_by_slot,
                pv_charge_power_kw=(
                    bms_power_kw
                    if bms_power_kw is not None
                    else system_power_kw
                ),
            ),
            metadata,
        )

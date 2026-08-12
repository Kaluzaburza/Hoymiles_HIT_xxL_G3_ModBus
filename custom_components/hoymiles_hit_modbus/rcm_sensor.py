"""Home Assistant sensor for RCEm 253 V+ voltage-aware PV buffering."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
import logging
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .models import RuntimeData
from .rcm_history import (
    GRID_VOLTAGE_ENTITIES,
    SLOTS_PER_DAY,
    VoltageHistorySummary,
    summarize_voltage_history,
)
from .rcm_optimizer import (
    RCMOptimizerInput,
    RCMRiskWindowInput,
    optimize_rcm,
    select_rcm_load_profile,
    select_rcm_pv_profile,
)
from .rce_optimizer import floor_half_hour
from .rce_sensor import (
    TODAY_FORECAST_CANDIDATES,
    TOMORROW_FORECAST_CANDIDATES,
    _detailed_pv_map,
    _first_numeric_state,
    _select_number,
    _state_number,
    _state_text,
)


_LOGGER = logging.getLogger(__name__)

WATCHED_RCM_ENTITIES = {
    *GRID_VOLTAGE_ENTITIES,
    "sensor.hoymiles_hit_overview_pv_total_power",
    "sensor.hoymiles_actual_load_power",
    "sensor.hoymiles_rce_grid_export_power",
    "sensor.hoymiles_hit_battery_capacity",
    "sensor.hoymiles_hit_overview_battery_soc",
    "sensor.hoymiles_hit_battery_voltage_bms",
    "sensor.hoymiles_hit_maximum_charge_current",
    "sensor.hoymiles_hit_maximum_discharge_current",
    "sensor.hoymiles_hit_number_of_machines_master_and_slave",
    "sensor.hoymiles_hit_rce_optimized_plan",
    "input_text.hoymiles_solcast_forecast_today_entity",
    "input_text.hoymiles_solcast_forecast_tomorrow_entity",
    *TODAY_FORECAST_CANDIDATES,
    *TOMORROW_FORECAST_CANDIDATES,
    "number.hoymiles_hit_self_use_soc",
    "number.hoymiles_hit_battery_max_charge_power",
    "number.hoymiles_hit_maximum_export_power_limit",
    "input_number.hoymiles_rcm_soc_safety_margin",
    "input_number.hoymiles_rcm_saved_battery_charge_power",
    "input_number.hoymiles_rcm_export_cap_percent",
    "input_number.hoymiles_rcm_saved_export_limit",
    "input_number.hoymiles_rcm_charge_efficiency",
    "input_select.hoymiles_rce_inverter_rated_power",
    "input_boolean.hoymiles_rcm_enabled",
    "input_boolean.hoymiles_rcm_shadow_mode",
    "input_boolean.hoymiles_rcm_export_control_enabled",
    "input_boolean.hoymiles_rcm_export_control_active",
}


@dataclass(frozen=True, slots=True)
class RCMEnergyForecast:
    """Selected energy profiles and balances for the next risk horizon."""

    surplus_kwh: float
    horizon: str
    selected_forecast_kwh: float
    selected_forecast_p90_kwh: float | None
    expected_load_kwh: float
    natural_headroom_kwh: float
    pre_risk_surplus_kwh: float
    unavoidable_charge_input_kwh: float
    minutes_to_risk: int | None
    risk_day_offset: int
    forecast_entity_id: str
    forecast_profile_source: str
    forecast_profile_confidence: float
    load_profile_source: str
    load_profile_confidence: float
    window_forecasts: tuple[RCMRiskWindowInput, ...]

STATUS_TEXT = {
    "pl": {
        "ready": "Gotowa — napięcie bezpieczne",
        "learning": "Uczenie — trwa zbieranie historii napięcia",
        "preparing_headroom": "Przygotowanie miejsca w magazynie",
        "preparing_discharge": "Poranne przygotowanie miejsca — kontrolowane rozładowanie",
        "controlling": "Regulacja — ochrona eksportu przed 253 V",
        "battery_limited": "Ograniczenie baterii — brak dalszej mocy ładowania",
        "emergency": "Ochrona szybka — napięcie co najmniej 253 V",
        "missing_data": "Brak wymaganych danych — sterowanie zablokowane",
        "optimizer_error": "Błąd obliczeń — sterowanie zablokowane",
    },
    "en": {
        "ready": "Ready — grid voltage safe",
        "learning": "Learning — collecting voltage history",
        "preparing_headroom": "Preparing battery headroom",
        "preparing_discharge": "Morning headroom preparation — controlled discharge",
        "controlling": "Regulating — protecting export below 253 V",
        "battery_limited": "Battery limited — no additional charge power",
        "emergency": "Fast protection — voltage at or above 253 V",
        "missing_data": "Required data missing — control blocked",
        "optimizer_error": "Calculation error — control blocked",
    },
}


def _minutes_text(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    day = "+1d " if minutes >= 24 * 60 else ""
    minute = minutes % (24 * 60)
    return f"{day}{minute // 60:02d}:{minute % 60:02d}"


def _window_text(window: tuple[int, int, float]) -> str:
    start, end, peak = window
    return f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d} ({peak:.1f} V)"


class HoymilesRCMOptimizerSensor(SensorEntity):
    """Expose voltage history, battery headroom and a safe charge setpoint."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "rcm_voltage_plan"
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: RuntimeData,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_rcm_voltage_plan"
        self._history = VoltageHistorySummary(
            history_days=0,
            sample_count=0,
            profile_median_v=(0.0,) * SLOTS_PER_DAY,
            profile_p90_v=(0.0,) * SLOTS_PER_DAY,
            daily_peak_v={},
            risk_windows=(),
        )
        self._samples: deque[tuple[datetime, float]] = deque()
        self._history_refresh_running = False
        self._attributes: dict[str, Any] = {
            "status_code": "missing_data",
            "missing_entities": [],
            "risk_windows": [],
        }

    @property
    def suggested_object_id(self) -> str:
        return "hoymiles_hit_rcm_voltage_plan"

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
        return STATUS_TEXT[language].get(code, STATUS_TEXT[language]["optimizer_error"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                sorted(WATCHED_RCM_ENTITIES),
                self._async_input_changed,
            )
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_control_timer,
                timedelta(seconds=15),
            )
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_history_timer,
                timedelta(hours=1),
            )
        )
        await self._async_refresh_voltage_history()
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _async_input_changed(self, event: Event[EventStateChangedData]) -> None:
        if event.data["entity_id"] in GRID_VOLTAGE_ENTITIES:
            self._append_voltage_sample()
            return
        self._recalculate_and_write()

    @callback
    def _async_control_timer(self, now: datetime) -> None:
        self._append_voltage_sample(now)
        self._recalculate_and_write()

    async def _async_history_timer(self, now: datetime) -> None:
        await self._async_refresh_voltage_history()
        self._recalculate_and_write()

    def _recalculate_and_write(self) -> None:
        """Write only when the voltage plan materially changed."""
        previous_state = self.native_value
        previous_attributes = self._attributes
        self._recalculate()
        if previous_state != self.native_value or previous_attributes != self._attributes:
            self.async_write_ha_state()

    def _append_voltage_sample(self, now: datetime | None = None) -> None:
        values = [_state_number(self.hass, entity_id) for entity_id in GRID_VOLTAGE_ENTITIES]
        if any(value is None for value in values):
            return
        timestamp = now or dt_util.now()
        self._samples.append((timestamp, max(value or 0.0 for value in values)))
        cutoff = timestamp - timedelta(minutes=10)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    async def _async_refresh_voltage_history(self) -> None:
        if self._history_refresh_running:
            return
        self._history_refresh_running = True
        try:
            timezone = ZoneInfo(self.hass.config.time_zone)
            now = dt_util.now().astimezone(timezone)
            start = now - timedelta(days=5)
            query = partial(
                recorder_history.get_significant_states,
                self.hass,
                dt_util.as_utc(start),
                dt_util.as_utc(now),
                list(GRID_VOLTAGE_ENTITIES),
                None,
                True,
                False,
                False,
                True,
            )
            raw = await get_recorder_instance(self.hass).async_add_executor_job(query)
            normalized: dict[str, list[tuple[datetime, float]]] = {
                entity_id: [] for entity_id in GRID_VOLTAGE_ENTITIES
            }
            for entity_id in GRID_VOLTAGE_ENTITIES:
                for item in raw.get(entity_id, []):
                    updated = getattr(item, "last_updated", None)
                    value = getattr(item, "state", None)
                    if updated is None or value is None:
                        continue
                    try:
                        numeric = float(value)
                    except (TypeError, ValueError):
                        continue
                    normalized[entity_id].append((updated.astimezone(timezone), numeric))
            self._history = summarize_voltage_history(normalized, now=now)
        except Exception:  # noqa: BLE001 - remain fail-closed
            _LOGGER.exception("Cannot rebuild four-day RCEm voltage history")
        finally:
            self._history_refresh_running = False

    def _expected_risk_surplus_kwh(
        self,
        system_power_kw: float,
    ) -> RCMEnergyForecast:
        """Estimate each risk window from Solcast and weekday/weekend LOAD."""
        missing = RCMEnergyForecast(
            surplus_kwh=0.0,
            horizon="missing",
            selected_forecast_kwh=0.0,
            selected_forecast_p90_kwh=None,
            expected_load_kwh=0.0,
            natural_headroom_kwh=0.0,
            pre_risk_surplus_kwh=0.0,
            unavoidable_charge_input_kwh=0.0,
            minutes_to_risk=None,
            risk_day_offset=-1,
            forecast_entity_id="",
            forecast_profile_source="missing",
            forecast_profile_confidence=0.0,
            load_profile_source="missing",
            load_profile_confidence=0.0,
            window_forecasts=(),
        )
        rce = self.hass.states.get("sensor.hoymiles_hit_rce_optimized_plan")
        if rce is None:
            return missing
        remaining = rce.attributes.get("forecast_remaining_today_kwh")
        tomorrow = rce.attributes.get("forecast_tomorrow_kwh")
        try:
            forecast_today = max(float(remaining or 0.0), 0.0)
            forecast_tomorrow = max(float(tomorrow or 0.0), 0.0)
        except (TypeError, ValueError):
            return missing

        timezone = ZoneInfo(self.hass.config.time_zone)
        now = dt_util.now().astimezone(timezone)
        minute = now.hour * 60 + now.minute
        current_slot = now.hour * 2 + now.minute // 30
        pending_today = sorted(
            (start, end, peak)
            for start, end, peak in self._history.risk_windows
            if end > minute
        )
        if pending_today:
            forecast = forecast_today
            horizon = "today"
            first_slot = current_slot
            risk_day_offset = 0
            first_risk_start = pending_today[0][0]
            minutes_to_risk = max(first_risk_start - minute, 0)
            selected_windows = pending_today
        elif self._history.risk_windows:
            forecast = forecast_tomorrow
            horizon = "tomorrow"
            first_slot = 0
            risk_day_offset = 1
            first_risk_start = min(
                start for start, _end, _peak in self._history.risk_windows
            )
            minutes_to_risk = 24 * 60 - minute + first_risk_start
            selected_windows = list(self._history.risk_windows)
        else:
            forecast = forecast_tomorrow
            horizon = "none"
            first_slot = 0
            risk_day_offset = -1
            first_risk_start = 0
            minutes_to_risk = None
            selected_windows = []
        # With no learned window we still expose tomorrow's diagnostic profile,
        # matching the selected forecast sensor instead of labelling it today.
        target_date = now.date() + timedelta(
            days=0 if risk_day_offset == 0 else 1
        )
        weekend = target_date.weekday() >= 5
        try:
            average_daily = max(
                float(rce.attributes.get("selected_average_daily_load_kwh", 0.0)),
                0.0,
            )
        except (TypeError, ValueError):
            average_daily = 0.0
        load_selection = select_rcm_load_profile(
            weekend=weekend,
            average_profile=rce.attributes.get("recorder_load_profile_30m_kwh"),
            weekday_profile=rce.attributes.get(
                "recorder_load_weekday_profile_30m_kwh"
            ),
            weekend_profile=rce.attributes.get(
                "recorder_load_weekend_profile_30m_kwh"
            ),
            average_daily_kwh=average_daily,
        )

        p90_key = (
            "forecast_today_p90_kwh"
            if risk_day_offset == 0
            else "forecast_tomorrow_p90_kwh"
        )
        raw_key = (
            "forecast_today_raw_kwh"
            if risk_day_offset == 0
            else "forecast_tomorrow_raw_kwh"
        )
        try:
            p90_raw = float(rce.attributes[p90_key])
            expected_raw = float(rce.attributes[raw_key])
            selected_p90 = (
                forecast
                * min(
                    max(max(p90_raw, expected_raw) / expected_raw, 1.0),
                    2.5,
                )
                if expected_raw > 0
                else None
            )
        except (KeyError, TypeError, ValueError):
            selected_p90 = None

        configured_entity = _state_text(
            self.hass,
            "input_text.hoymiles_solcast_forecast_today_entity"
            if risk_day_offset == 0
            else "input_text.hoymiles_solcast_forecast_tomorrow_entity",
        )
        forecast_entity, forecast_state = _first_numeric_state(
            self.hass,
            TODAY_FORECAST_CANDIDATES
            if risk_day_offset == 0
            else TOMORROW_FORECAST_CANDIDATES,
            configured_entity,
        )
        now_slot = (
            floor_half_hour(now)
            if risk_day_offset == 0
            else datetime.combine(target_date, datetime.min.time(), tzinfo=timezone)
        )
        p50_map = _detailed_pv_map(
            forecast_state,
            target_date,
            forecast,
            timezone,
            now_slot,
            percentile="p50",
        )
        p90_map = (
            _detailed_pv_map(
                forecast_state,
                target_date,
                selected_p90,
                timezone,
                now_slot,
                percentile="p90",
            )
            if selected_p90 is not None
            else {}
        )
        p50_by_slot = {
            start.hour * 2 + start.minute // 30: energy
            for start, energy in p50_map.items()
        }
        p90_by_slot = {
            start.hour * 2 + start.minute // 30: energy
            for start, energy in p90_map.items()
        }
        risk_slots = tuple(
            sorted(
                {
                    slot
                    for start, end, _peak in selected_windows
                    for slot in range(start // 30, min((end + 29) // 30, 48))
                }
            )
        )
        pv_selection = select_rcm_pv_profile(
            forecast_total_kwh=forecast,
            forecast_p90_total_kwh=selected_p90,
            detailed_p50_by_slot=p50_by_slot,
            detailed_p90_by_slot=p90_by_slot,
            first_slot=first_slot,
            current_slot_fraction=(
                (30 - now.minute % 30) / 30.0
                if risk_day_offset == 0
                else 1.0
            ),
            risk_slots=risk_slots,
        )
        horizon_start_minute = minute if risk_day_offset == 0 else 0
        minimum_charge_floor_kw = max(system_power_kw, 0.0) * 0.10

        def energy_balance(
            start_minute: int,
            end_minute: int,
        ) -> tuple[float, float, float, float, float]:
            pv_energy = 0.0
            load_energy = 0.0
            surplus_energy = 0.0
            deficit_energy = 0.0
            minimum_charge_input = 0.0
            for slot in range(
                max(start_minute // 30, first_slot),
                min((end_minute + 29) // 30, 48),
            ):
                slot_start = slot * 30
                slot_end = slot_start + 30
                available_start = max(horizon_start_minute, slot_start)
                overlap_start = max(start_minute, available_start)
                overlap_end = min(end_minute, slot_end)
                overlap_minutes = max(overlap_end - overlap_start, 0)
                if overlap_minutes <= 0:
                    continue
                available_minutes = max(slot_end - available_start, 1)
                slot_pv = (
                    pv_selection.slot_kwh[slot]
                    * overlap_minutes
                    / available_minutes
                )
                slot_load = (
                    load_selection.slot_kwh[slot]
                    * overlap_minutes
                    / 30.0
                )
                pv_energy += slot_pv
                load_energy += slot_load
                surplus_energy += max(slot_pv - slot_load, 0.0)
                deficit_energy += max(slot_load - slot_pv, 0.0)
                minimum_charge_input += min(
                    max(slot_pv - slot_load, 0.0),
                    minimum_charge_floor_kw * overlap_minutes / 60.0,
                )
            return (
                pv_energy,
                load_energy,
                surplus_energy,
                deficit_energy,
                minimum_charge_input,
            )

        window_forecasts: list[RCMRiskWindowInput] = []
        for start, end, peak in selected_windows:
            window_start = max(start, horizon_start_minute)
            (
                pv_energy,
                load_energy,
                surplus_energy,
                _deficit,
                _minimum_charge,
            ) = energy_balance(
                window_start,
                end,
            )
            (
                _pre_pv,
                _pre_load,
                _pre_surplus,
                natural_before,
                _pre_minimum_charge,
            ) = energy_balance(
                horizon_start_minute,
                max(start, horizon_start_minute),
            )
            window_forecasts.append(
                RCMRiskWindowInput(
                    start_minute=start,
                    end_minute=end,
                    peak_voltage_v=peak,
                    day_offset=risk_day_offset,
                    expected_pv_kwh=pv_energy,
                    expected_load_kwh=load_energy,
                    expected_surplus_kwh=surplus_energy,
                    natural_headroom_before_kwh=natural_before,
                )
            )
        first_balance = energy_balance(
            horizon_start_minute,
            max(first_risk_start, horizon_start_minute),
        )
        return RCMEnergyForecast(
            surplus_kwh=sum(item.expected_surplus_kwh for item in window_forecasts),
            horizon=horizon,
            selected_forecast_kwh=forecast,
            selected_forecast_p90_kwh=selected_p90,
            expected_load_kwh=sum(item.expected_load_kwh for item in window_forecasts),
            natural_headroom_kwh=(
                window_forecasts[0].natural_headroom_before_kwh
                if window_forecasts
                else 0.0
            ),
            pre_risk_surplus_kwh=first_balance[2],
            unavoidable_charge_input_kwh=first_balance[4],
            minutes_to_risk=minutes_to_risk,
            risk_day_offset=risk_day_offset,
            forecast_entity_id=forecast_entity,
            forecast_profile_source=pv_selection.source,
            forecast_profile_confidence=pv_selection.confidence,
            load_profile_source=load_selection.source,
            load_profile_confidence=load_selection.confidence,
            window_forecasts=tuple(window_forecasts),
        )

    def _recalculate(self) -> None:
        try:
            required = {
                entity_id: _state_number(self.hass, entity_id)
                for entity_id in (
                    *GRID_VOLTAGE_ENTITIES,
                    "sensor.hoymiles_hit_overview_pv_total_power",
                    "sensor.hoymiles_actual_load_power",
                    "sensor.hoymiles_hit_battery_capacity",
                    "sensor.hoymiles_hit_overview_battery_soc",
                    "number.hoymiles_hit_self_use_soc",
                    "number.hoymiles_hit_battery_max_charge_power",
                    "input_number.hoymiles_rcm_soc_safety_margin",
                    "input_number.hoymiles_rcm_charge_efficiency",
                    "input_number.hoymiles_rcm_export_cap_percent",
                )
            }
            export_control_enabled = self.hass.states.is_state(
                "input_boolean.hoymiles_rcm_export_control_enabled",
                "on",
            )
            current_export_limit = _state_number(
                self.hass,
                "number.hoymiles_hit_maximum_export_power_limit",
            )
            if export_control_enabled and current_export_limit is None:
                required[
                    "number.hoymiles_hit_maximum_export_power_limit"
                ] = None
            rated_power = _select_number(
                self.hass,
                "input_select.hoymiles_rce_inverter_rated_power",
            )
            if rated_power is None:
                required["input_select.hoymiles_rce_inverter_rated_power"] = None
            missing = sorted(key for key, value in required.items() if value is None)
            if missing:
                self._attributes = {
                    "status_code": "missing_data",
                    "missing_entities": missing,
                    "risk_windows": [_window_text(item) for item in self._history.risk_windows],
                    "history_days": self._history.history_days,
                }
                return

            now = dt_util.now().astimezone(ZoneInfo(self.hass.config.time_zone))
            self._append_voltage_sample(now)
            recent = [value for timestamp, value in self._samples if timestamp >= now - timedelta(seconds=60)]
            rolling = [value for _timestamp, value in self._samples]
            live_max = max(required[entity_id] or 0.0 for entity_id in GRID_VOLTAGE_ENTITIES)
            filtered = median(recent) if recent else live_max
            rolling_10m = sum(rolling) / len(rolling) if rolling else live_max
            current_slot = now.hour * 4 + now.minute // 15
            historical_p90 = self._history.profile_p90_v[current_slot]
            inverter_count = min(
                max(
                    round(
                        _state_number(
                            self.hass,
                            "sensor.hoymiles_hit_number_of_machines_master_and_slave",
                        )
                        or 1.0
                    ),
                    1,
                ),
                10,
            )
            saved_limit = _state_number(
                self.hass,
                "input_number.hoymiles_rcm_saved_battery_charge_power",
            )
            if saved_limit is None or saved_limit < 10.0:
                saved_limit = required["number.hoymiles_hit_battery_max_charge_power"]
            system_power_kw = rated_power * inverter_count
            energy_forecast = self._expected_risk_surplus_kwh(system_power_kw)
            rce_plan = self.hass.states.get(
                "sensor.hoymiles_hit_rce_optimized_plan"
            )
            protected_minimum_soc = (
                rce_plan.attributes.get("minimum_soc")
                if rce_plan is not None
                else None
            )
            try:
                protected_minimum_soc = float(protected_minimum_soc)
            except (TypeError, ValueError):
                protected_minimum_soc = (
                    required["number.hoymiles_hit_self_use_soc"]
                    + required["input_number.hoymiles_rcm_soc_safety_margin"]
                )
            saved_export_limit = _state_number(
                self.hass,
                "input_number.hoymiles_rcm_saved_export_limit",
            )
            if not self.hass.states.is_state(
                "input_boolean.hoymiles_rcm_export_control_active",
                "on",
            ):
                saved_export_limit = current_export_limit or 0.0
            elif saved_export_limit is None:
                saved_export_limit = current_export_limit or 0.0
            result = optimize_rcm(
                RCMOptimizerInput(
                    now=now,
                    voltage_l1_v=required[GRID_VOLTAGE_ENTITIES[0]],
                    voltage_l2_v=required[GRID_VOLTAGE_ENTITIES[1]],
                    voltage_l3_v=required[GRID_VOLTAGE_ENTITIES[2]],
                    filtered_voltage_v=filtered,
                    rolling_10m_voltage_v=rolling_10m,
                    historical_p90_voltage_v=historical_p90,
                    risk_windows=self._history.risk_windows,
                    history_days=self._history.history_days,
                    pv_power_kw=max(required["sensor.hoymiles_hit_overview_pv_total_power"], 0.0) / 1000.0,
                    load_power_kw=max(required["sensor.hoymiles_actual_load_power"], 0.0) / 1000.0,
                    grid_export_power_kw=max(
                        _state_number(self.hass, "sensor.hoymiles_rce_grid_export_power") or 0.0,
                        0.0,
                    ),
                    battery_capacity_kwh=required["sensor.hoymiles_hit_battery_capacity"],
                    battery_soc_percent=required["sensor.hoymiles_hit_overview_battery_soc"],
                    reserve_soc_percent=required["number.hoymiles_hit_self_use_soc"],
                    safety_margin_soc_percent=required["input_number.hoymiles_rcm_soc_safety_margin"],
                    protected_minimum_soc_percent=protected_minimum_soc,
                    expected_risk_surplus_kwh=energy_forecast.surplus_kwh,
                    expected_natural_headroom_kwh=(
                        energy_forecast.natural_headroom_kwh
                    ),
                    minutes_to_risk=energy_forecast.minutes_to_risk,
                    risk_day_offset=energy_forecast.risk_day_offset,
                    system_power_kw=system_power_kw,
                    battery_voltage_v=_state_number(self.hass, "sensor.hoymiles_hit_battery_voltage_bms"),
                    bms_max_charge_current_a=_state_number(self.hass, "sensor.hoymiles_hit_maximum_charge_current"),
                    bms_max_discharge_current_a=_state_number(
                        self.hass,
                        "sensor.hoymiles_hit_maximum_discharge_current",
                    ),
                    current_charge_limit_percent=required["number.hoymiles_hit_battery_max_charge_power"],
                    saved_charge_limit_percent=saved_limit,
                    export_control_enabled=export_control_enabled,
                    current_export_limit_percent=current_export_limit or 0.0,
                    saved_export_limit_percent=saved_export_limit,
                    user_export_cap_percent=required[
                        "input_number.hoymiles_rcm_export_cap_percent"
                    ],
                    charge_efficiency_percent=required["input_number.hoymiles_rcm_charge_efficiency"],
                    expected_pre_risk_surplus_kwh=(
                        energy_forecast.pre_risk_surplus_kwh
                    ),
                    risk_window_forecasts=energy_forecast.window_forecasts,
                    expected_unavoidable_charge_input_kwh=(
                        energy_forecast.unavoidable_charge_input_kwh
                    ),
                )
            )
            risk_window_details = [
                {
                    "start": (
                        f"{item.start_minute // 60:02d}:"
                        f"{item.start_minute % 60:02d}"
                    ),
                    "end": (
                        f"{item.end_minute // 60:02d}:"
                        f"{item.end_minute % 60:02d}"
                    ),
                    "peak_voltage_v": item.peak_voltage_v,
                    "day_offset": item.day_offset,
                    "expected_pv_kwh": item.expected_pv_kwh,
                    "expected_load_kwh": item.expected_load_kwh,
                    "expected_surplus_kwh": item.expected_surplus_kwh,
                    "required_headroom_kwh": item.required_headroom_kwh,
                    "projected_headroom_before_kwh": (
                        item.projected_headroom_before_kwh
                    ),
                    "cumulative_headroom_shortfall_kwh": (
                        item.cumulative_headroom_shortfall_kwh
                    ),
                }
                for item in result.risk_window_plans
            ]
            self._attributes = {
                "status_code": result.status_code,
                "missing_entities": [],
                "enabled": self.hass.states.is_state("input_boolean.hoymiles_rcm_enabled", "on"),
                "shadow_mode": self.hass.states.is_state("input_boolean.hoymiles_rcm_shadow_mode", "on"),
                "action": result.action,
                "voltage_l1_v": round(required[GRID_VOLTAGE_ENTITIES[0]], 2),
                "voltage_l2_v": round(required[GRID_VOLTAGE_ENTITIES[1]], 2),
                "voltage_l3_v": round(required[GRID_VOLTAGE_ENTITIES[2]], 2),
                "maximum_voltage_v": result.maximum_voltage_v,
                "filtered_voltage_v": round(filtered, 2),
                "rolling_10m_voltage_v": round(rolling_10m, 2),
                "historical_p90_voltage_v": (
                    round(historical_p90, 3)
                    if self._history.history_days > 0 and historical_p90 > 0
                    else None
                ),
                "historical_p90_available": (
                    self._history.history_days > 0 and historical_p90 > 0
                ),
                "historical_p90_slot_index": current_slot,
                "historical_p90_slot_start": (
                    f"{current_slot // 4:02d}:{(current_slot % 4) * 15:02d}"
                ),
                "historical_p90_source": "recorder_15m_four_day_profile",
                "voltage_risk_score_percent": result.voltage_risk_score,
                "risk_window_active": result.risk_window_active,
                "next_risk_start": _minutes_text(result.next_risk_start_minute),
                "risk_windows": [_window_text(item) for item in self._history.risk_windows],
                "history_days": self._history.history_days,
                "history_samples": self._history.sample_count,
                "history_daily_peak_v": self._history.daily_peak_v,
                "history_profile_median_v": list(self._history.profile_median_v),
                "history_profile_p90_v": [
                    round(value, 3) if value > 0 else None
                    for value in self._history.profile_p90_v
                ],
                "reserve_soc_percent": result.reserve_soc_percent,
                "protected_minimum_soc_percent": result.protected_minimum_soc_percent,
                "required_headroom_kwh": result.required_headroom_kwh,
                "available_headroom_kwh": result.available_headroom_kwh,
                "headroom_shortfall_kwh": result.headroom_shortfall_kwh,
                "expected_natural_headroom_kwh": result.expected_natural_headroom_kwh,
                "unavoidable_minimum_charge_kwh": (
                    result.unavoidable_minimum_charge_kwh
                ),
                "unavoidable_charge_before_risk_kwh": (
                    result.unavoidable_minimum_charge_kwh
                ),
                "planned_grid_discharge_kwh": result.planned_grid_discharge_kwh,
                "pre_discharge_target_soc_percent": result.pre_discharge_target_soc_percent,
                "pre_discharge_power_kw": result.pre_discharge_power_kw,
                "pre_discharge_power_percent": result.pre_discharge_power_percent,
                "pre_discharge_ready": result.pre_discharge_ready,
                "minutes_to_risk": energy_forecast.minutes_to_risk,
                "risk_day_offset": energy_forecast.risk_day_offset,
                "target_soc_before_risk_percent": result.target_soc_before_risk_percent,
                "expected_risk_surplus_kwh": round(
                    energy_forecast.surplus_kwh,
                    3,
                ),
                "risk_surplus_horizon": energy_forecast.horizon,
                "selected_forecast_kwh": round(
                    energy_forecast.selected_forecast_kwh,
                    3,
                ),
                "selected_forecast_p90_kwh": (
                    round(energy_forecast.selected_forecast_p90_kwh, 3)
                    if energy_forecast.selected_forecast_p90_kwh is not None
                    else None
                ),
                "forecast_entity_id": energy_forecast.forecast_entity_id,
                "forecast_profile_source": (
                    energy_forecast.forecast_profile_source
                ),
                "pv_profile_source": energy_forecast.forecast_profile_source,
                "forecast_profile_confidence_percent": round(
                    energy_forecast.forecast_profile_confidence * 100.0,
                    1,
                ),
                "pv_profile_confidence_percent": round(
                    energy_forecast.forecast_profile_confidence * 100.0,
                    1,
                ),
                "load_profile_source": energy_forecast.load_profile_source,
                "load_profile_mode": energy_forecast.load_profile_source,
                "load_profile_confidence_percent": round(
                    energy_forecast.load_profile_confidence * 100.0,
                    1,
                ),
                "expected_risk_load_kwh": round(
                    energy_forecast.expected_load_kwh,
                    3,
                ),
                "expected_pre_risk_surplus_kwh": round(
                    energy_forecast.pre_risk_surplus_kwh,
                    3,
                ),
                "risk_window_details": risk_window_details,
                "risk_window_energy_plans": risk_window_details,
                "pv_surplus_power_kw": result.pv_surplus_power_kw,
                "bms_charge_power_limit_kw": result.bms_charge_power_limit_kw,
                "recommended_charge_limit_percent": result.recommended_charge_limit_percent,
                "recommended_charge_power_kw": result.recommended_charge_power_kw,
                "export_control_enabled": export_control_enabled,
                "effective_export_cap_percent": result.effective_export_cap_percent,
                "recommended_export_limit_percent": result.recommended_export_limit_percent,
                "estimated_safe_export_power_kw": result.estimated_safe_export_power_kw,
                "estimated_safe_export_available": (
                    result.estimated_safe_export_power_kw is not None
                ),
                "estimated_safe_export_reason": (
                    "live_pv_surplus"
                    if result.estimated_safe_export_power_kw is not None
                    else "not_applicable_no_pv_surplus"
                ),
                "battery_limit_saturated": result.saturated,
                "actuators": [
                    "number.hoymiles_hit_battery_max_charge_power",
                    "number.hoymiles_hit_maximum_export_power_limit"
                    if export_control_enabled
                    else "export_control_disabled",
                ],
                "protected_entities": [
                    "select.hoymiles_hit_generation_control_function",
                    "three_phase_unbalance",
                    "grid_protection_settings",
                ],
            }
        except Exception:  # noqa: BLE001 - automation must fail closed
            _LOGGER.exception("Cannot calculate the RCEm voltage plan")
            self._attributes = {
                "status_code": "optimizer_error",
                "missing_entities": [],
                "risk_windows": [],
            }

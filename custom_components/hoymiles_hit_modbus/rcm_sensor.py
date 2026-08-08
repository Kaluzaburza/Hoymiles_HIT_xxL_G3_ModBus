"""Home Assistant sensor for RCEm 253 V+ voltage-aware PV buffering."""

from __future__ import annotations

from collections import deque
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
from .rcm_optimizer import RCMOptimizerInput, optimize_rcm
from .rce_sensor import _select_number, _state_number


_LOGGER = logging.getLogger(__name__)

WATCHED_RCM_ENTITIES = {
    *GRID_VOLTAGE_ENTITIES,
    "sensor.hoymiles_hit_overview_pv_total_power",
    "sensor.hoymiles_actual_load_power",
    "sensor.hoymiles_rce_grid_export_power",
    "sensor.hoymiles_hit_total_capacity",
    "sensor.hoymiles_hit_overview_battery_soc",
    "sensor.hoymiles_hit_battery_voltage_bms",
    "sensor.hoymiles_hit_maximum_charge_current",
    "sensor.hoymiles_hit_maximum_discharge_current",
    "sensor.hoymiles_hit_number_of_machines_master_and_slave",
    "sensor.hoymiles_hit_rce_optimized_plan",
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
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _async_control_timer(self, now: datetime) -> None:
        self._append_voltage_sample(now)
        self._recalculate()
        self.async_write_ha_state()

    async def _async_history_timer(self, now: datetime) -> None:
        await self._async_refresh_voltage_history()
        self._recalculate()
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
    ) -> tuple[float, str, float, float, float, int | None, int]:
        """Estimate risk surplus and natural battery use before that risk.

        The return value contains the PV surplus in the next relevant risk
        window, its horizon and forecast, the expected load during the risk,
        natural battery discharge before the risk, minutes until the risk and
        a day offset (0=today, 1=tomorrow, -1=no known risk window).
        """
        rce = self.hass.states.get("sensor.hoymiles_hit_rce_optimized_plan")
        if rce is None:
            return 0.0, "missing", 0.0, 0.0, 0.0, None, -1
        remaining = rce.attributes.get("forecast_remaining_today_kwh")
        tomorrow = rce.attributes.get("forecast_tomorrow_kwh")
        profile = rce.attributes.get("recorder_load_profile_30m_kwh")
        try:
            forecast_today = max(float(remaining or 0.0), 0.0)
            forecast_tomorrow = max(float(tomorrow or 0.0), 0.0)
        except (TypeError, ValueError):
            return 0.0, "missing", 0.0, 0.0, 0.0, None, -1
        if not isinstance(profile, (list, tuple)) or len(profile) != 48:
            average_daily = rce.attributes.get("selected_average_daily_load_kwh", 0.0)
            try:
                load_profile = [max(float(average_daily), 0.0) / 48.0] * 48
            except (TypeError, ValueError):
                load_profile = [0.0] * 48
        else:
            try:
                load_profile = [max(float(value), 0.0) for value in profile]
            except (TypeError, ValueError):
                load_profile = [0.0] * 48

        now = dt_util.now().astimezone(ZoneInfo(self.hass.config.time_zone))
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

        # Without detailed interval attributes here, distribute the selected
        # Solcast total over the daylight still belonging to this horizon.
        weights = [0.0] * 48
        for slot in range(max(12, first_slot), 42):
            distance = abs(slot - 27) / 15.0
            weights[slot] = max(1.0 - distance * distance, 0.0)
        if risk_day_offset == 0 and 0 <= current_slot < 48:
            # ``forecast_remaining_today_kwh`` starts at the current instant,
            # therefore the first half-hour slot must contain only its
            # unelapsed fraction.
            weights[current_slot] *= (30 - now.minute % 30) / 30.0
        total_weight = sum(weights) or 1.0
        pv_per_day = [forecast * weight / total_weight for weight in weights]
        risk_overlap_minutes = [0] * 48
        horizon_start_minute = minute if risk_day_offset == 0 else 0
        for start, end, _peak in selected_windows:
            for slot in range(max(start // 30, first_slot), min((end + 29) // 30, 48)):
                slot_start = slot * 30
                slot_end = slot_start + 30
                overlap = max(
                    min(end, slot_end)
                    - max(start, horizon_start_minute, slot_start),
                    0,
                )
                risk_overlap_minutes[slot] = min(
                    risk_overlap_minutes[slot] + overlap,
                    30,
                )
        surplus = 0.0
        risk_load = 0.0
        for slot, overlap_minutes in enumerate(risk_overlap_minutes):
            if overlap_minutes <= 0:
                continue
            slot_start = slot * 30
            slot_end = slot_start + 30
            available_minutes = slot_end - max(horizon_start_minute, slot_start)
            pv_energy = (
                pv_per_day[slot]
                * overlap_minutes
                / max(available_minutes, 1)
            )
            load_energy = load_profile[slot] * overlap_minutes / 30.0
            surplus += max(pv_energy - load_energy, 0.0)
            risk_load += load_energy
        natural_headroom = 0.0
        if risk_day_offset == 0 and first_risk_start > minute:
            # In Self-Use only the part of LOAD not supplied by PV discharges
            # the battery.  This energy creates headroom naturally and must be
            # subtracted before any deliberate Grid Discharge is planned.
            pre_risk_end_slot = min((first_risk_start + 29) // 30, 48)
            for slot in range(current_slot, pre_risk_end_slot):
                slot_start = slot * 30
                slot_end = slot_start + 30
                overlap_start = max(minute, slot_start)
                overlap_end = min(first_risk_start, slot_end)
                overlap_minutes = max(overlap_end - overlap_start, 0)
                if overlap_minutes <= 0:
                    continue
                load_energy = load_profile[slot] * overlap_minutes / 30.0
                available_minutes = slot_end - max(minute, slot_start)
                pv_energy = (
                    pv_per_day[slot]
                    * overlap_minutes
                    / max(available_minutes, 1)
                )
                natural_headroom += max(load_energy - pv_energy, 0.0)
        return (
            surplus,
            horizon,
            forecast,
            risk_load,
            natural_headroom,
            minutes_to_risk,
            risk_day_offset,
        )

    def _recalculate(self) -> None:
        try:
            required = {
                entity_id: _state_number(self.hass, entity_id)
                for entity_id in (
                    *GRID_VOLTAGE_ENTITIES,
                    "sensor.hoymiles_hit_overview_pv_total_power",
                    "sensor.hoymiles_actual_load_power",
                    "sensor.hoymiles_hit_total_capacity",
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
            (
                expected_risk_surplus,
                risk_surplus_horizon,
                selected_forecast_kwh,
                expected_risk_load_kwh,
                expected_natural_headroom_kwh,
                minutes_to_risk,
                risk_day_offset,
            ) = (
                self._expected_risk_surplus_kwh()
            )
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
                    battery_capacity_kwh=required["sensor.hoymiles_hit_total_capacity"],
                    battery_soc_percent=required["sensor.hoymiles_hit_overview_battery_soc"],
                    reserve_soc_percent=required["number.hoymiles_hit_self_use_soc"],
                    safety_margin_soc_percent=required["input_number.hoymiles_rcm_soc_safety_margin"],
                    protected_minimum_soc_percent=protected_minimum_soc,
                    expected_risk_surplus_kwh=expected_risk_surplus,
                    expected_natural_headroom_kwh=expected_natural_headroom_kwh,
                    minutes_to_risk=minutes_to_risk,
                    risk_day_offset=risk_day_offset,
                    system_power_kw=rated_power * inverter_count,
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
                )
            )
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
                "historical_p90_voltage_v": round(historical_p90, 2),
                "voltage_risk_score_percent": result.voltage_risk_score,
                "risk_window_active": result.risk_window_active,
                "next_risk_start": _minutes_text(result.next_risk_start_minute),
                "risk_windows": [_window_text(item) for item in self._history.risk_windows],
                "history_days": self._history.history_days,
                "history_samples": self._history.sample_count,
                "history_daily_peak_v": self._history.daily_peak_v,
                "history_profile_median_v": list(self._history.profile_median_v),
                "history_profile_p90_v": list(self._history.profile_p90_v),
                "reserve_soc_percent": result.reserve_soc_percent,
                "protected_minimum_soc_percent": result.protected_minimum_soc_percent,
                "required_headroom_kwh": result.required_headroom_kwh,
                "available_headroom_kwh": result.available_headroom_kwh,
                "headroom_shortfall_kwh": result.headroom_shortfall_kwh,
                "expected_natural_headroom_kwh": result.expected_natural_headroom_kwh,
                "planned_grid_discharge_kwh": result.planned_grid_discharge_kwh,
                "pre_discharge_target_soc_percent": result.pre_discharge_target_soc_percent,
                "pre_discharge_power_kw": result.pre_discharge_power_kw,
                "pre_discharge_power_percent": result.pre_discharge_power_percent,
                "pre_discharge_ready": result.pre_discharge_ready,
                "minutes_to_risk": minutes_to_risk,
                "risk_day_offset": risk_day_offset,
                "target_soc_before_risk_percent": result.target_soc_before_risk_percent,
                "expected_risk_surplus_kwh": round(expected_risk_surplus, 2),
                "risk_surplus_horizon": risk_surplus_horizon,
                "selected_forecast_kwh": round(selected_forecast_kwh, 2),
                "expected_risk_load_kwh": round(expected_risk_load_kwh, 2),
                "pv_surplus_power_kw": result.pv_surplus_power_kw,
                "bms_charge_power_limit_kw": result.bms_charge_power_limit_kw,
                "recommended_charge_limit_percent": result.recommended_charge_limit_percent,
                "recommended_charge_power_kw": result.recommended_charge_power_kw,
                "export_control_enabled": export_control_enabled,
                "effective_export_cap_percent": result.effective_export_cap_percent,
                "recommended_export_limit_percent": result.recommended_export_limit_percent,
                "estimated_safe_export_power_kw": result.estimated_safe_export_power_kw,
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

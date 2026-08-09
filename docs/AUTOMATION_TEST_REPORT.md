# Automation simulation report

Date: 2026-08-09 (Europe/Warsaw)
Validated release: **v1.4.5**

This report covers the pure planning logic used by the RCE market-price
optimizer, tariff-aware grid charging and RCEm 253 V+ voltage management. The
tests do not write to Home Assistant or an inverter.

## Representative systems

| System | Inverters | Battery | Daily PV | Home/day | Home/night | BMS current |
|---|---:|---:|---:|---:|---:|---:|
| HIT-10 | 1 × 10 kW | 10.2 kWh | 12 kWh | 8 kWh | 3.2 kWh | 100 A |
| HIT-15 | 1 × 15 kW | 21 kWh | 28 kWh | 16 kWh | 6.5 kWh | 175 A |
| HIT-20 | 1 × 20 kW | 40 kWh | 55 kWh | 28 kWh | 11 kWh | 250 A |
| Parallel HIT-20 | 2 × 20 kW | 230 kWh | 120 kWh | 48 kWh | 19 kWh | 700 A |

The values are deliberately representative rather than manufacturer claims.
They combine undersized and oversized storage, weak and strong PV, ordinary
and winter-like load, and BMS limits below inverter power.

## Full matrix result

**2064/2064 scenarios passed.**

- RCE: 576 scenarios — 209 ready, 11 home protected and 356 intentional home
  energy shortage states.
- Tariff charging: 720 scenarios — 14 ready, 224 no charge needed, 322
  insufficient cheap window, 122 no discount window, 32 shortage in a cheap
  period and 6 no cheap window.
- RCEm: 648 scenarios — 378 controlling, 108 emergency, 72 ready, 54 battery
  limited and 36 preparing pre-discharge.
- Reproducible randomized RCE boundary sweep: 120 scenarios.
- Home Assistant interlock markers for RCE, tariff charging, RCEm, battery
  balancing and manual charge/discharge timers are present.

## Boundaries exercised

- inverter power: 10, 15, 20 and 2 × 20 kW;
- battery SOC: below reserve, mid-range and nearly full;
- PV: none, severe forecast miss, normal production and overflow;
- RCE: today's peak, tomorrow's higher peak, volatile prices, negative prices
  and blocked 22:00–06:00 slots;
- tariffs: G11, G12, G12w and G13, weekday/weekend, morning/noon/end-of-window;
- physical charge lead time: 10 kWh at 10 kW starts at 14:00 for a 15:00 peak;
  at 5 kW it uses four half-hour blocks; home load during Grid Charge reduces
  the power available to the battery and moves the start earlier;
- RCEm voltage: 240.0–253.2 V, 0/50/100% user export caps, full battery,
  insufficient headroom and BMS-limited charge/discharge power;
- power and energy invariants for every half-hour slot, SOC reserve floors,
  export lockout, efficiencies and end-of-horizon battery bounds.

## Existing deterministic regression suites

- RCE optimizer: 14 named scenarios passed;
- RCE Recorder reconstruction passed;
- official 2026 tariff profile schedules and prices passed;
- tariff optimizer deterministic suite passed;
- tariff active-window regression passed: the contiguous slot end and target
  remain stable while live SOC and forecast inputs are recalculated;
- RCEm repeated-window/outlier history suite passed;
- RCEm safety, headroom and BMS-limit suite passed.
- RCE chart, zebra entities card, battery-capacity conversion and PL/EN dynamic
  dashboard strategy JavaScript validation passed;
- responsive glance wrapping, native graph definitions and the four current
  documentation views were validated without dashboard configuration errors.

The quick 488-scenario matrix runs on every push and pull request. The complete
matrix runs on the scheduled GitHub workflow and manual workflow dispatch.

## Remaining field-test limits

The simulations validate planning arithmetic and safety invariants, not the
inverter firmware, Modbus transport, wiring or a distribution grid. RCEm still
requires observation on a real high-voltage export site before it should be
described as field-proven. Parallel Master/Slave command propagation remains
dependent on the physical inverter topology and firmware. Users must retain
the inverter's certified grid protections and BMS limits.

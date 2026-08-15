const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(
  "home_assistant/www/hoymiles-rce-chart-card.js",
  "utf8",
);
const bootstrapSource = fs.readFileSync(
  "home_assistant/www/hoymiles-dashboard-strategy.js",
  "utf8",
);
if (!source.includes("import.meta.url")) {
  throw new Error("Dashboard strategy no longer resolves assets from its module URL");
}
// The card is loaded as an ES module by Home Assistant.  vm.runInNewContext
// executes classic scripts, so inject the same deterministic module URL while
// retaining an explicit assertion above that production code uses import.meta.
const canonicalModuleUrl =
  "https://homeassistant.example/local/hoymiles-rce-chart-card.js?v=1.5.5.17";
const executableSource = source.replaceAll(
  "import.meta.url",
  JSON.stringify(canonicalModuleUrl),
);
const registry = new Map();

class FakeNode {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.children = [];
    this.innerHTML = "";
    this.dataset = {};
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  addEventListener() {}

  querySelector() {
    return null;
  }

  querySelectorAll() {
    return [];
  }
}

class TestElement {
  attachShadow() {
    this.shadowRoot = new FakeNode("shadow-root");
    return this.shadowRoot;
  }

  dispatchEvent(event) {
    this.lastEvent = event;
    return true;
  }
}

const context = {
  console,
  CustomEvent: class {
    constructor(type, options) {
      this.type = type;
      Object.assign(this, options);
    }
  },
  Date,
  document: {
    documentElement: { lang: "pl" },
    createElement(tagName) {
      return new FakeNode(tagName);
    },
  },
  HTMLElement: TestElement,
  Intl,
  URL,
  fetch: async (url, options) => {
    const match = String(url).match(
      /^https:\/\/homeassistant\.example\/local\/dashboard_hoymiles_(pl|en)\.json$/,
    );
    if (!match) {
      throw new Error(`Unexpected dashboard request: ${url}`);
    }
    if (options?.cache !== "no-store") {
      throw new Error("Dashboard strategy must bypass the browser cache");
    }
    const payload = JSON.parse(
      fs.readFileSync(
        `custom_components/hoymiles_hit_modbus/resources/www/dashboard_hoymiles_${match[1]}.json`,
        "utf8",
      ),
    );
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      async json() {
        return payload;
      },
    };
  },
  window: {
    async loadCardHelpers() {
      return {
        createCardElement(config) {
          const card = new FakeNode("hui-card");
          card.config = config;
          card.getCardSize = () => 4;
          card.getGridOptions = () => ({ columns: 8, rows: 4 });
          card.updateComplete = Promise.resolve();
          return card;
        },
      };
    },
  },
  customElements: {
    define(name, constructor) {
      registry.set(name, constructor);
    },
    get(name) {
      return registry.get(name);
    },
    async whenDefined() {},
  },
};

vm.runInNewContext(executableSource, context, {
  filename: "hoymiles-rce-chart-card.js",
});

const customCardCount = context.window.customCards?.length ?? 0;
const canonicalStrategy = registry.get(
  "ll-strategy-dashboard-hoymiles-hit-xxl-g3",
);
vm.runInNewContext(
  executableSource,
  {
    ...context,
    window: context.window,
    customElements: context.customElements,
  },
  { filename: "hoymiles-rce-chart-card-second-load.js" },
);
if ((context.window.customCards?.length ?? 0) !== customCardCount) {
  throw new Error("Loading the frontend module twice duplicated card metadata");
}
if (
  !canonicalStrategy ||
  registry.get("ll-strategy-dashboard-hoymiles-hit-xxl-g3") !== canonicalStrategy
) {
  throw new Error("Duplicate module loading replaced the canonical dashboard strategy");
}

// A stale storage resource from an older release can execute the classic
// bootstrap before the canonical module on the first page load after an
// update. The immutable custom-element registration must still be upgraded to
// the full decorated generate() implementation when the canonical module runs.
const bootstrapFirstRegistry = new Map();
const bootstrapFirstWindow = {
  loadCardHelpers: context.window.loadCardHelpers,
};
const bootstrapFirstContext = {
  ...context,
  document: {
    ...context.document,
    currentScript: {
      src: (
        "https://homeassistant.example/local/"
        + "hoymiles-dashboard-strategy.js?v=1.5.5.17"
      ),
    },
  },
  window: bootstrapFirstWindow,
  customElements: {
    define(name, constructor) {
      bootstrapFirstRegistry.set(name, constructor);
    },
    get(name) {
      return bootstrapFirstRegistry.get(name);
    },
    async whenDefined() {},
  },
};
vm.runInNewContext(bootstrapSource, bootstrapFirstContext, {
  filename: "hoymiles-dashboard-strategy-bootstrap-first.js",
});
const bootstrapFirstStrategy = bootstrapFirstRegistry.get(
  "ll-strategy-dashboard-hoymiles-hit-xxl-g3",
);
if (!bootstrapFirstStrategy) {
  throw new Error("Bootstrap-first fixture did not register its legacy strategy");
}
vm.runInNewContext(executableSource, bootstrapFirstContext, {
  filename: "hoymiles-rce-chart-card-after-bootstrap.js",
});
if (
  bootstrapFirstRegistry.get("ll-strategy-dashboard-hoymiles-hit-xxl-g3")
  !== bootstrapFirstStrategy
) {
  throw new Error("Canonical module attempted to redefine a bootstrap-first strategy");
}

const Card = registry.get("hoymiles-rce-chart-card");
if (!Card) {
  throw new Error("The RCE custom element was not registered");
}

const ZebraEntitiesCard = registry.get("hoymiles-zebra-entities-card");
if (!ZebraEntitiesCard) {
  throw new Error("The zebra entities custom element was not registered");
}
if (
  !context.window.customCards?.some(
    (card) => card.type === "hoymiles-zebra-entities-card",
  )
) {
  throw new Error("The zebra entities card is absent from custom-card metadata");
}
console.log("Zebra entities card: registered without duplicate metadata");

const DiagnosticsDownloadCard = registry.get(
  "hoymiles-diagnostics-download-card",
);
if (!DiagnosticsDownloadCard) {
  throw new Error("The diagnostics download custom element was not registered");
}
for (const expected of [
  "/api/hoymiles_hit_modbus/support-bundle",
  "this._hass.fetchWithAuth(",
  "this._hass.user?.is_admin",
  "response.blob()",
  "URL.createObjectURL(blob)",
  "Zbierz dane i pobierz ZIP",
  "info@kaluzaaa.com",
]) {
  if (!source.includes(expected)) {
    throw new Error(`Diagnostics download card is missing: ${expected}`);
  }
}
console.log("Diagnostics download card: registered with browser ZIP handling");

const ResponsiveGlanceCard = registry.get("hoymiles-responsive-glance-card");
if (!ResponsiveGlanceCard) {
  throw new Error("The responsive glance custom element was not registered");
}
const responsiveGlance = new ResponsiveGlanceCard();
if (!responsiveGlance.shadowRoot) {
  throw new Error("Responsive glance must own its layout shadow root");
}
for (const expected of [
  "grid-template-columns: repeat(auto-fit",
  "overflow-wrap: anywhere",
  'new CustomEvent("hass-more-info"',
]) {
  if (!source.includes(expected)) {
    throw new Error(`Responsive glance is missing: ${expected}`);
  }
}
if (source.includes('document.createElement("hui-glance-card")')) {
  throw new Error("Responsive glance delegated layout back to fixed-column hui-glance-card");
}
console.log("Responsive glance: wrapping layout registered successfully");

for (const token of [
  "--hoymiles-aurora-surface",
  "--hoymiles-aurora-border",
  "--hoymiles-aurora-text",
  "--hoymiles-aurora-muted",
  "--hoymiles-aurora-pv",
  "--hoymiles-aurora-load",
  "--hoymiles-aurora-grid",
  "--hoymiles-aurora-battery",
  "--hoymiles-aurora-good",
  "--hoymiles-aurora-warn",
  "--hoymiles-aurora-error",
]) {
  if (!source.includes(token)) {
    throw new Error(`Shared Aurora theme is missing token: ${token}`);
  }
}
for (const forbidden of ["custom:card-mod", "custom:mushroom-"]) {
  if (source.includes(forbidden)) {
    throw new Error(`Aurora frontend introduced a forbidden dependency: ${forbidden}`);
  }
}

const auroraTypes = [
  "hoymiles-aurora-frame-card",
  "hoymiles-aurora-status-card",
  "hoymiles-aurora-history-card",
  "hoymiles-aurora-finance-card",
];
for (const type of auroraTypes) {
  if (!registry.get(type)) {
    throw new Error(`Aurora custom element was not registered: ${type}`);
  }
  const metadataCount = context.window.customCards?.filter(
    (item) => item.type === type,
  ).length ?? 0;
  if (metadataCount !== 1) {
    throw new Error(`Aurora metadata count for ${type} is ${metadataCount}, expected 1`);
  }
}

const AuroraFrameCard = registry.get("hoymiles-aurora-frame-card");
const frameCard = new AuroraFrameCard();
let missingNestedCardRejected = false;
try {
  frameCard.setConfig({ accent: "pv" });
} catch (_error) {
  missingNestedCardRejected = true;
}
if (!missingNestedCardRejected) {
  throw new Error("Aurora frame accepted a configuration without nested card");
}
frameCard.setConfig({
  accent: "not-a-real-accent",
  view_layout: { position: "sidebar" },
  card: { type: "history-graph", hours_to_show: 24 },
});
if (frameCard._config.accent !== "neutral") {
  throw new Error("Aurora frame did not safely normalize an unknown accent");
}
if (frameCard._config.card.type !== "history-graph") {
  throw new Error("Aurora frame changed its nested native card configuration");
}
const initialFrameHass = { states: { "sensor.ready": { state: "on" } } };
frameCard.hass = initialFrameHass;
frameCard.isConnected = true;
const auroraFrameMountPromise = frameCard._mount().then(() => {
  if (
    frameCard._card?.config?.type !== "history-graph" ||
    frameCard._card?.config?.hours_to_show !== 24
  ) {
    throw new Error("Aurora frame did not pass the nested card config unchanged");
  }
  if (frameCard._card?.hass !== initialFrameHass) {
    throw new Error("Aurora frame did not forward hass supplied before mount");
  }
  if (
    frameCard.getCardSize() !== 4 ||
    frameCard.getGridOptions()?.columns !== 8
  ) {
    throw new Error("Aurora frame did not delegate child card dimensions");
  }
  const updatedHass = { states: { "sensor.ready": { state: "off" } } };
  frameCard.hass = updatedHass;
  if (frameCard._card?.hass !== updatedHass) {
    throw new Error("Aurora frame did not forward hass after mount");
  }
});

const AuroraStatusCard = registry.get("hoymiles-aurora-status-card");
const statusCard = new AuroraStatusCard();
statusCard._config = { language: "pl", details_path: "stany-alarmy" };
for (const [stateValue, alarm, expectedTone] of [
  ["Praca z siecią", false, "good"],
  ["Czuwanie", false, "good"],
  ["Off-grid", false, "warn"],
  ["Brak sieci", false, "warn"],
  ["Offline", false, "error"],
  ["Niedostępne", false, "offline"],
  ["No fault", true, "good"],
  ["no_errors", true, "good"],
  ["Brak błędów", true, "good"],
  ["3", true, "error"],
  ["unavailable", true, "offline"],
]) {
  const tone = statusCard._toneForState({ state: stateValue }, alarm);
  if (tone !== expectedTone) {
    throw new Error(`Aurora status tone for ${stateValue} is ${tone}, expected ${expectedTone}`);
  }
}
if (statusCard._detailsPath() !== "/hoymiles-falownik/stany-alarmy") {
  throw new Error("Aurora status relative details path was resolved incorrectly");
}
statusCard.isConnected = true;
statusCard.setConfig({
  setup_entity: "sensor.setup",
  alarm_entities: ["sensor.optional_alarm"],
});
const goodStatusStates = Object.fromEntries(
  [
    statusCard._config.system_entity,
    statusCard._config.inverter_entity,
    statusCard._config.meter_entity,
    statusCard._config.battery_entity,
    statusCard._config.parallel_entity,
    statusCard._config.setup_entity,
  ].map((entity) => [entity, { state: "OK", last_changed: "2026-08-11T10:00:00Z" }]),
);
goodStatusStates["sensor.optional_alarm"] = {
  state: "unavailable",
  last_changed: "2026-08-11T10:00:00Z",
};
statusCard.hass = { language: "pl", states: goodStatusStates };
if (
  !statusCard.shadowRoot.innerHTML.includes("System działa prawidłowo") ||
  statusCard._summaryTone !== "good"
) {
  throw new Error("Aurora status did not render the compact Polish OK state");
}
statusCard.hass = {
  language: "en",
  states: {
    ...goodStatusStates,
    [statusCard._config.inverter_entity]: {
      state: "Offline",
      last_changed: "2026-08-11T10:00:00Z",
    },
  },
};
if (
  !statusCard.shadowRoot.innerHTML.includes("System fault detected") ||
  statusCard._summaryTone !== "error"
) {
  throw new Error("Aurora status did not expand for an English inverter fault");
}
context.hoymilesDispatchMoreInfo(statusCard, "sensor.target");
if (
  statusCard.lastEvent?.type !== "hass-more-info" ||
  statusCard.lastEvent?.detail?.entityId !== "sensor.target"
) {
  throw new Error("Aurora status emitted an invalid more-info event");
}
for (const expected of [
  'new CustomEvent("hass-more-info"',
  "setup_entity",
  "last_changed",
  "alarm_entities",
]) {
  if (!source.includes(expected)) {
    throw new Error(`Aurora status card is missing: ${expected}`);
  }
}

const AuroraHistoryCard = registry.get("hoymiles-aurora-history-card");
const englishHistoryCard = new AuroraHistoryCard();
englishHistoryCard._loading = true;
const historyRequestVersion = englishHistoryCard._requestVersion;
englishHistoryCard.setConfig({ language: "en", entities: ["sensor.pv"] });
if (
  englishHistoryCard._config.title ||
  englishHistoryCard._copy().title !== "Power — last 24 hours"
) {
  throw new Error("Aurora history did not use its translated default title");
}
if (
  englishHistoryCard._loading ||
  englishHistoryCard._requestVersion !== historyRequestVersion + 1
) {
  throw new Error("Aurora history did not invalidate an in-flight request on reconfigure");
}
const historyCard = new AuroraHistoryCard();
historyCard.setConfig({
  hours_to_show: 24,
  entities: [
    { entity: "sensor.pv", name: "PV", color: "#2de083" },
    { entity: "sensor.grid", name: "Grid", color: "invalid" },
  ],
});
historyCard._hass = {
  states: {
    "sensor.pv": { state: "1550", attributes: { unit_of_measurement: "W" } },
    "sensor.grid": { state: "-2.5", attributes: { unit_of_measurement: "kW" } },
  },
};
if (historyCard._currentValue("sensor.pv") !== 1.55) {
  throw new Error("Aurora history did not convert W history/current data to kW");
}
if (historyCard._currentValue("sensor.grid") !== -2.5) {
  throw new Error("Aurora history changed the signed grid value");
}
if (historyCard._config.entities[1].color !== "#ff5d73") {
  throw new Error("Aurora history did not replace an unsafe series color");
}
const historyEnd = Date.parse("2026-08-11T12:00:00Z");
const normalizedHistory = historyCard._normalizeHistory(
  [[
    {
      entity_id: "sensor.pv",
      state: "1000",
      last_changed: "2026-08-11T11:00:00Z",
    },
  ]],
  historyEnd - 24 * 60 * 60 * 1000,
  historyEnd,
);
if (normalizedHistory.get("sensor.pv")?.[0]?.value !== 1) {
  throw new Error("Aurora history did not normalize recorder data");
}
if (historyCard._powerKw("unavailable", "sensor.pv") !== null) {
  throw new Error("Aurora history did not safely ignore unavailable data");
}
const historySource = source.slice(
  source.indexOf("class HoymilesAuroraHistoryCard"),
  source.indexOf("class HoymilesAuroraFinanceCard"),
);
for (const expected of [
  'this._hass.callApi("GET", path)',
  "linearGradient",
  "@container (max-width: 360px)",
  "prefers-reduced-motion: reduce",
]) {
  if (!historySource.includes(expected)) {
    throw new Error(`Aurora history card is missing: ${expected}`);
  }
}
if (historySource.includes("setInterval(") || historySource.includes("setTimeout(")) {
  throw new Error("Aurora history introduced a timer that can leak after disconnect");
}

const AuroraFinanceCard = registry.get("hoymiles-aurora-finance-card");
const financeCard = new AuroraFinanceCard();
financeCard.setConfig({});
financeCard._hass = {
  states: {
    "sensor.hoymiles_rce_revenue_daily": { state: "unavailable", attributes: {} },
    "sensor.hoymiles_rce_grid_export_energy_daily": {
      state: "12500",
      attributes: { unit_of_measurement: "Wh" },
    },
    "sensor.hoymiles_rce_grid_export_power": {
      state: "3500",
      attributes: { unit_of_measurement: "W" },
    },
  },
};
if (financeCard._numeric("sensor.hoymiles_rce_revenue_daily") !== null) {
  throw new Error("Aurora finance did not safely handle unavailable revenue");
}
if (financeCard._energy("sensor.hoymiles_rce_grid_export_energy_daily") !== 12.5) {
  throw new Error("Aurora finance did not convert exported Wh to kWh");
}
if (financeCard._power("sensor.hoymiles_rce_grid_export_power") !== 3.5) {
  throw new Error("Aurora finance did not convert export W to kW");
}
console.log("Aurora frame/status/history/finance: registration and safe data handling OK");

const AuroraCard = registry.get("hoymiles-aurora-energy-card");
if (!AuroraCard) {
  throw new Error("The Aurora energy custom element was not registered");
}
if (
  !context.window.customCards?.some(
    (item) => item.type === "hoymiles-aurora-energy-card",
  )
) {
  throw new Error("The Aurora card is absent from custom-card metadata");
}
for (const expected of [
  "container-type: inline-size",
  "prefers-reduced-motion: reduce",
  'data-ribbon="pv"',
  'data-flow="battery"',
  'data-label="grid_import_title"',
  'data-key="grid_import_today"',
  'data-key="grid_to_load_today"',
  'data-key="grid_to_battery_today"',
  'new CustomEvent("hass-more-info"',
]) {
  if (!source.includes(expected)) {
    throw new Error(`Aurora card is missing: ${expected}`);
  }
}
const auroraCard = new AuroraCard();
auroraCard.setConfig({
  pv_entity: "sensor.pv_w",
  grid_entity: "sensor.grid_kw",
});
auroraCard._hass = {
  language: "pl",
  states: {
    "sensor.pv_w": {
      state: "1550",
      attributes: { unit_of_measurement: "W" },
    },
    "sensor.grid_kw": {
      state: "-2.5",
      attributes: { unit_of_measurement: "kW" },
    },
    "sensor.hoymiles_hit_grid_energy_buy_today": {
      state: "12.5",
      attributes: { unit_of_measurement: "kWh" },
    },
  },
};
if (auroraCard._powerKw("pv") !== 1.55) {
  throw new Error("Aurora card did not convert W to kW");
}
if (auroraCard._powerKw("grid") !== -2.5) {
  throw new Error("Aurora card changed the signed kW grid value");
}
if (auroraCard._formatPower(1.55) !== "1,55 kW") {
  throw new Error("Aurora card does not format power with two decimals");
}
if (
  auroraCard._entityId("grid_import_today") !==
    "sensor.hoymiles_hit_grid_energy_buy_today" ||
  auroraCard._entityId("grid_to_load_today") !==
    "sensor.hoymiles_rce_grid_to_load_today" ||
  auroraCard._entityId("grid_to_battery_today") !==
    "sensor.hoymiles_grid_to_battery_today"
) {
  throw new Error("Aurora card grid-import strip defaults changed");
}
if (auroraCard._formatEnergy("grid_import_today") !== "12,5 kWh") {
  throw new Error("Aurora card did not format today's grid import energy");
}
for (const language of ["pl", "en"]) {
  const dashboard = JSON.parse(
    fs.readFileSync(
      `custom_components/hoymiles_hit_modbus/resources/www/dashboard_hoymiles_${language}.json`,
      "utf8",
    ),
  );
  const start = dashboard.views?.find((view) => view.path === "start");
  if (
    !start?.cards?.some(
      (item) => item.type === "custom:hoymiles-aurora-energy-card",
    )
  ) {
    throw new Error(`The ${language} Start view does not contain Aurora`);
  }
}
console.log("Aurora energy card: registration, units and dashboard payloads OK");

const card = new Card();
card.setConfig({
  type: "custom:hoymiles-rce-chart-card",
  entity: "sensor.hoymiles_rce_day",
  plan_entity: "sensor.hoymiles_hit_rce_optimized_plan",
  current_price_entity: "sensor.hoymiles_rce_current_price",
  active_entity: "input_boolean.hoymiles_rce_discharge_active",
  block_enabled_entity: "input_boolean.hoymiles_sale_block_enabled",
  block_start_entity: "input_datetime.hoymiles_sale_block_start",
  block_end_entity: "input_datetime.hoymiles_sale_block_end",
});

const timestamp = "2026-07-26T10:00:00+02:00";
const state = (value, attributes = {}) => ({
  state: String(value),
  attributes,
  last_updated: timestamp,
});
const rows = Array.from({ length: 96 }, (_, index) => ({
  business_date: "2026-07-26",
  period: `${String(Math.floor(index / 4)).padStart(2, "0")}:${String(
    (index % 4) * 15,
  ).padStart(2, "0")}`,
  rce_pln: 450 + index * 5,
}));

card.hass = {
  language: "pl",
  states: {
    "sensor.hoymiles_rce_day": state("2026-07-26", { value: rows }),
    "sensor.hoymiles_hit_rce_optimized_plan": state("Gotowa", {
      automatic_price_floor_pln_kwh: 0.9,
      planned_slots: [
        {
          date: "2026-07-26",
          start: "20:00",
          end: "20:30",
          price: 0.9,
          energy: 2.5,
          revenue: 2.25,
        },
        {
          date: "2026-07-27",
          start: "06:00",
          end: "06:30",
          price: 1.1,
          energy: 2.5,
          revenue: 2.75,
        },
      ],
    }),
    "sensor.hoymiles_rce_current_price": state("0.669"),
    "input_boolean.hoymiles_rce_discharge_active": state("off"),
    "input_boolean.hoymiles_sale_block_enabled": state("on"),
    "input_datetime.hoymiles_sale_block_start": state("22:00:00"),
    "input_datetime.hoymiles_sale_block_end": state("06:00:00"),
  },
};

const output = card.shadowRoot?.innerHTML || "";
for (const expected of [
  "<svg",
  "PLN/kWh",
  "96 okresów po 15 min",
  "48 bloków sterowania po 30 min",
  "Cena graniczna planu",
  "22:00–06:00",
  "1 × 30 min",
  "2,50 kWh",
  "2,25 PLN",
]) {
  if (!output.includes(expected)) {
    throw new Error(`Rendered RCE card is missing: ${expected}`);
  }
}

console.log("RCE custom card: registered and rendered successfully");

const PowerFlowCard = registry.get("hoymiles-power-flow-card");
if (!PowerFlowCard) {
  throw new Error("The Hoymiles power-flow custom element was not registered");
}
const powerFlowCard = new PowerFlowCard();
powerFlowCard._config = {
  battery: {
    energy: "sensor.hoymiles_hit_battery_capacity",
  },
};
const resolvedCapacity = powerFlowCard._resolveBatteryEnergy({
  states: {
    "sensor.hoymiles_hit_battery_capacity": state("230", {
      unit_of_measurement: "kWh",
    }),
  },
});
if (resolvedCapacity.value !== 230000) {
  throw new Error(
    `Battery capacity was not converted from kWh to Wh: ${resolvedCapacity.value}`,
  );
}
const unavailableCapacity = powerFlowCard._resolveBatteryEnergy({ states: {} });
if (unavailableCapacity.value !== 0) {
  throw new Error("Unavailable battery capacity must disable runtime estimates");
}
console.log("Power-flow battery capacity: entity converted to Wh successfully");

const denseHistory = Array.from({ length: 1000 }, (_value, index) => ({
  time: index * 1000,
  value: index === 337 ? 42 : index === 663 ? -17 : Math.sin(index / 30),
}));
const reducedHistory = historyCard._downsample(denseHistory, 120);
if (
  reducedHistory.length > 120 ||
  !reducedHistory.some((point) => point.value === 42) ||
  !reducedHistory.some((point) => point.value === -17)
) {
  throw new Error("Aurora history downsampling did not preserve extrema");
}
const accuratePath = historyCard._path(
  denseHistory.slice(0, 4),
  (value) => value / 1000,
  (value) => value,
);
if (accuratePath.includes("Q") || !accuratePath.includes("L")) {
  throw new Error("Aurora history path can visually overshoot measured data");
}
console.log("Aurora history: extrema-preserving reduction and accurate path OK");

if (typeof context.hoymilesDecorateCard !== "function") {
  throw new Error("Runtime Aurora dashboard decorator is not exposed");
}
const decoratedLeaf = context.hoymilesDecorateCard(
  {
    type: "markdown",
    content: "Status",
    view_layout: { position: "sidebar" },
    visibility: [{ condition: "state", entity: "binary_sensor.ready", state: "on" }],
    grid_options: { columns: 6 },
  },
  "pv",
);
if (
  decoratedLeaf.type !== "custom:hoymiles-aurora-frame-card" ||
  decoratedLeaf.accent !== "pv" ||
  decoratedLeaf.card?.type !== "markdown" ||
  decoratedLeaf.view_layout?.position !== "sidebar" ||
  decoratedLeaf.grid_options?.columns !== 6 ||
  decoratedLeaf.visibility?.[0]?.entity !== "binary_sensor.ready"
) {
  throw new Error("Runtime Aurora decorator did not wrap and hoist a native leaf card");
}
for (const hoisted of ["view_layout", "visibility", "grid_options"]) {
  if (hoisted in decoratedLeaf.card) {
    throw new Error(`Runtime Aurora decorator left ${hoisted} inside the child card`);
  }
}
if (
  JSON.stringify(context.hoymilesDecorateCard(decoratedLeaf, "load")) !==
  JSON.stringify(decoratedLeaf)
) {
  throw new Error("Runtime Aurora decorator is not idempotent");
}
const decoratedConditional = context.hoymilesDecorateCard(
  {
    type: "conditional",
    conditions: [{ entity: "input_boolean.details", state: "on" }],
    card: { type: "statistic", entity: "sensor.energy" },
  },
  "grid",
);
if (
  decoratedConditional.type !== "conditional" ||
  decoratedConditional.card?.type !== "custom:hoymiles-aurora-frame-card" ||
  decoratedConditional.card?.accent !== "grid"
) {
  throw new Error("Runtime Aurora decorator did not recurse through a conditional card");
}
const existingAurora = { type: "custom:hoymiles-aurora-energy-card", pv_entity: "sensor.pv" };
const decoratedGrid = context.hoymilesDecorateCard(
  {
    type: "grid",
    columns: 2,
    cards: [
      { type: "tile", entity: "switch.ems", tap_action: { action: "toggle" } },
      { type: "button", entity: "switch.notifications", tap_action: { action: "toggle" } },
      { type: "statistic", entity: "sensor.energy" },
      existingAurora,
    ],
  },
  "ems",
);
if (
  decoratedGrid.type !== "grid" ||
  decoratedGrid.cards?.[0]?.type !== "tile" ||
  decoratedGrid.cards?.[0]?.tap_action?.action !== "toggle" ||
  decoratedGrid.cards?.[1]?.type !== "button" ||
  decoratedGrid.cards?.[1]?.tap_action?.action !== "toggle" ||
  decoratedGrid.cards?.[2]?.type !== "custom:hoymiles-aurora-frame-card" ||
  decoratedGrid.cards?.[2]?.accent !== "ems" ||
  decoratedGrid.cards?.[3]?.type !== "custom:hoymiles-aurora-energy-card"
) {
  throw new Error("Runtime Aurora decorator changed interactive controls or rewrapped an Aurora card");
}
for (const [label, expectedAccent] of [
  ["PV stringi", "pv"],
  ["Odbiór LOAD", "load"],
  ["Bateria", "battery"],
  ["Sieć i RCE", "grid"],
  ["Sterowanie EMS", "ems"],
  ["RCEm 253 V", "warning"],
]) {
  if (context.hoymilesAuroraTextAccent(label) !== expectedAccent) {
    throw new Error(`Aurora accent inference failed for ${label}`);
  }
}
console.log("Runtime Aurora decorator: recursion, hoisting and idempotence OK");

const Strategy = canonicalStrategy;
if (!Strategy) {
  throw new Error("The Hoymiles dashboard strategy was not registered");
}
if (
  !context.window.customStrategies?.some(
    (strategy) =>
      strategy.type === "hoymiles-hit-xxl-g3" &&
      strategy.strategyType === "dashboard",
  )
) {
  throw new Error("The dashboard strategy is absent from the community picker");
}

Promise.all([
  Strategy.generate({}, { locale: { language: "pl-PL" } }),
  Strategy.generate({}, { locale: { language: "en-GB" } }),
  bootstrapFirstStrategy.generate({}, { locale: { language: "pl-PL" } }),
  auroraFrameMountPromise,
])
  .then(([polishDashboard, englishDashboard, bootstrapFirstDashboard]) => {
    if (
      polishDashboard.views?.length < 10 ||
      englishDashboard.views?.length < 10
    ) {
      throw new Error("Dashboard strategy returned an incomplete dashboard");
    }
    const polishGrid = polishDashboard.views?.find(
      (view) => view.path === "siec",
    );
    const englishGrid = englishDashboard.views?.find(
      (view) => view.path === "siec",
    );
    if (polishGrid?.title !== "Sieć" || englishGrid?.title !== "Grid") {
      throw new Error("Dashboard strategy did not select the HA language");
    }
    const collectCards = (cards, found = []) => {
      for (const card of cards || []) {
        found.push(card);
        if (card?.type === "conditional" && card.card) {
          collectCards([card.card], found);
        } else if (Array.isArray(card?.cards)) {
          collectCards(card.cards, found);
        }
      }
      return found;
    };
    for (const dashboard of [
      polishDashboard,
      englishDashboard,
      bootstrapFirstDashboard,
    ]) {
      const allCards = dashboard.views.flatMap((view) => collectCards(view.cards));
      const frames = allCards.filter(
        (card) => card?.type === "custom:hoymiles-aurora-frame-card",
      );
      if (frames.length !== 42) {
        throw new Error(
          `Dashboard strategy produced ${frames.length} Aurora frames, expected 42`,
        );
      }
      if (
        frames.some(
          (card) => card.card?.type === "custom:hoymiles-aurora-frame-card",
        )
      ) {
        throw new Error("Dashboard strategy produced nested Aurora frames");
      }
      if (
        !allCards.some(
          (card) => card?.type === "custom:hoymiles-aurora-energy-card",
        )
      ) {
        throw new Error("Dashboard strategy lost the existing Aurora energy card");
      }
    }
    console.log(
      "Dashboard strategy: PL/EN and bootstrap-first paths render 42 Aurora frames",
    );
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });

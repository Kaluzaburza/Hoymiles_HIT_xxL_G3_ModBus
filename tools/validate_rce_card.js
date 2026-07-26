const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(
  "home_assistant/www/hoymiles-rce-chart-card.js",
  "utf8",
);
const registry = new Map();

class TestElement {
  attachShadow() {
    this.shadowRoot = { innerHTML: "" };
    return this.shadowRoot;
  }

  dispatchEvent() {
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
  document: { documentElement: { lang: "pl" } },
  HTMLElement: TestElement,
  Intl,
  window: {},
  customElements: {
    define(name, constructor) {
      registry.set(name, constructor);
    },
    get(name) {
      return registry.get(name);
    },
  },
};

vm.runInNewContext(source, context, {
  filename: "hoymiles-rce-chart-card.js",
});

const Card = registry.get("hoymiles-rce-chart-card");
if (!Card) {
  throw new Error("The RCE custom element was not registered");
}

const card = new Card();
card.setConfig({
  type: "custom:hoymiles-rce-chart-card",
  entity: "sensor.hoymiles_rce_day",
  threshold_entity: "input_number.hoymiles_rce_price_threshold",
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
    "input_number.hoymiles_rce_price_threshold": state("0.65"),
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
  "22:00–06:00",
]) {
  if (!output.includes(expected)) {
    throw new Error(`Rendered RCE card is missing: ${expected}`);
  }
}

console.log("RCE custom card: registered and rendered successfully");

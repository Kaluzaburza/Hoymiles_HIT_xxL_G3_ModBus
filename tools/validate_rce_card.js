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
  fetch: async (url, options) => {
    const match = String(url).match(/dashboard_hoymiles_(pl|en)\.json$/);
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

const customCardCount = context.window.customCards?.length ?? 0;
vm.runInNewContext(
  source,
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
    energy: "sensor.hoymiles_hit_total_capacity",
  },
};
const resolvedCapacity = powerFlowCard._resolveBatteryEnergy({
  states: {
    "sensor.hoymiles_hit_total_capacity": state("230", {
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

const Strategy = registry.get(
  "ll-strategy-dashboard-hoymiles-hit-xxl-g3",
);
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
])
  .then(([polishDashboard, englishDashboard]) => {
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
    console.log("Dashboard strategy: PL/EN payloads loaded successfully");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });

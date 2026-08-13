class HoymilesHitDashboardStrategy extends HTMLElement {
  static noEditor = true;

  static getCreateSuggestions(hass) {
    const language = (
      hass?.locale?.language ??
      hass?.language ??
      "en"
    ).toLowerCase();
    return {
      title: language.startsWith("pl")
        ? "Hoymiles — falownik"
        : "Hoymiles — inverter",
      icon: "mdi:solar-power-variant",
    };
  }

  static async generate(config, hass) {
    const requestedLanguage = (
      config?.language ??
      hass?.locale?.language ??
      hass?.language ??
      "en"
    ).toLowerCase();
    const language = requestedLanguage.startsWith("pl") ? "pl" : "en";
    const dashboardUrl = new URL(
      `dashboard_hoymiles_${language}.json`,
      import.meta.url
    );
    dashboardUrl.search = "";
    const response = await fetch(dashboardUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(
        `Cannot load Hoymiles dashboard (${response.status} ${response.statusText})`
      );
    }
    const dashboard = hoymilesDecorateDashboard(await response.json());
    if (config?.title) {
      dashboard.title = config.title;
    }
    return dashboard;
  }
}

// Register the dashboard strategy before the larger custom-card bundle.  A
// frontend card error must never prevent Lovelace from discovering the
// strategy and turning the whole dashboard into a timeout page.
if (!customElements.get("ll-strategy-dashboard-hoymiles-hit-xxl-g3")) {
  customElements.define(
    "ll-strategy-dashboard-hoymiles-hit-xxl-g3",
    HoymilesHitDashboardStrategy
  );
}

const HOYMILES_AURORA_ACCENTS = Object.freeze({
  neutral: "#66d9ff",
  cyan: "#66d9ff",
  pv: "#2de083",
  load: "#ff5d73",
  grid: "#ffc857",
  battery: "#3ea6ff",
  ems: "#a78bfa",
  warning: "#ff8a4c",
});

const HOYMILES_AURORA_THEME_CSS = `
  :host {
    --hoymiles-aurora-surface:
      radial-gradient(circle at 92% -10%, color-mix(in srgb, var(--hoymiles-aurora-accent, #66d9ff) 10%, transparent), transparent 38%),
      linear-gradient(145deg,
        color-mix(in srgb, var(--card-background-color, var(--ha-card-background)) 96%, var(--primary-text-color) 4%),
        var(--card-background-color, var(--ha-card-background)) 70%);
    --hoymiles-aurora-border: color-mix(in srgb, var(--hoymiles-aurora-accent, #66d9ff) 22%, var(--divider-color));
    --hoymiles-aurora-shadow: 0 14px 34px color-mix(in srgb, #000 18%, transparent);
    --hoymiles-aurora-text: var(--primary-text-color);
    --hoymiles-aurora-muted: var(--secondary-text-color);
    --hoymiles-aurora-pv: #2de083;
    --hoymiles-aurora-load: #ff5d73;
    --hoymiles-aurora-grid: #ffc857;
    --hoymiles-aurora-battery: #3ea6ff;
    --hoymiles-aurora-good: #2de083;
    --hoymiles-aurora-warn: #ffc857;
    --hoymiles-aurora-error: #ff5d73;
    --hoymiles-aurora-offline: #8da0b8;
    --ha-card-background: var(--hoymiles-aurora-surface);
    --ha-card-border-color: var(--hoymiles-aurora-border);
    --ha-card-border-radius: 20px;
    --ha-card-box-shadow: var(--hoymiles-aurora-shadow);
  }
`;

function hoymilesAuroraAccent(value) {
  return HOYMILES_AURORA_ACCENTS[value] || HOYMILES_AURORA_ACCENTS.neutral;
}

function hoymilesLanguage(hass, configuredLanguage) {
  return String(configuredLanguage || hass?.language || "en")
    .toLowerCase()
    .startsWith("pl")
    ? "pl"
    : "en";
}

function hoymilesEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function hoymilesDispatchMoreInfo(element, entityId) {
  if (!entityId) return;
  element.dispatchEvent(
    new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId },
    })
  );
}

function hoymilesAuroraTextAccent(input) {
  const value = String(input || "").toLowerCase();
  if (/rcem|253\s*v/.test(value)) return "warning";
  if (/load|eps|odbior|odbiór/.test(value)) return "load";
  if (/bateri|battery|magazyn/.test(value)) return "battery";
  if (/(^|[\s-])pv([\s-]|$)|produkcj|solar|(^|[\s-])gen([\s-]|$)|generator/.test(value)) return "pv";
  if (/rce|sieć|siec|grid|zysk|profit|revenue/.test(value)) return "grid";
  if (/ems|taryf|tariff|sterowan|control/.test(value)) return "ems";
  return "neutral";
}

function hoymilesAuroraViewAccent(view) {
  return hoymilesAuroraTextAccent(`${view?.path || ""} ${view?.title || ""}`);
}

function hoymilesDecorateCard(card, accent) {
  if (!card || typeof card !== "object" || Array.isArray(card)) return card;
  const type = String(card.type || "");
  if (type === "custom:hoymiles-aurora-frame-card") return card;

  if (type === "conditional" && card.card) {
    return { ...card, card: hoymilesDecorateCard(card.card, accent) };
  }
  if (
    [
      "vertical-stack",
      "horizontal-stack",
      "grid",
      "custom:hoymiles-responsive-stack-card",
    ].includes(type) &&
    Array.isArray(card.cards)
  ) {
    return {
      ...card,
      cards: card.cards.map((child) => hoymilesDecorateCard(child, accent)),
    };
  }

  const skipped =
    type.startsWith("custom:hoymiles-aurora-") ||
    [
      "custom:hoymiles-zebra-entities-card",
      "custom:hoymiles-responsive-glance-card",
      "custom:hoymiles-rce-chart-card",
    ].includes(type);
  if (skipped || !["history-graph", "statistics-graph", "markdown", "glance", "statistic"].includes(type)) {
    return card;
  }

  const {
    view_layout: viewLayout,
    visibility,
    grid_options: gridOptions,
    ...nestedCard
  } = card;
  return {
    type: "custom:hoymiles-aurora-frame-card",
    accent,
    ...(viewLayout ? { view_layout: viewLayout } : {}),
    ...(visibility ? { visibility } : {}),
    ...(gridOptions ? { grid_options: gridOptions } : {}),
    card: nestedCard,
  };
}

function hoymilesDecorateDashboard(dashboard) {
  if (!dashboard || !Array.isArray(dashboard.views)) return dashboard;
  return {
    ...dashboard,
    views: dashboard.views.map((view) => {
      const accent = hoymilesAuroraViewAccent(view);
      return {
        ...view,
        cards: Array.isArray(view.cards)
          ? view.cards.map((card) => hoymilesDecorateCard(card, accent))
          : view.cards,
      };
    }),
  };
}

class HoymilesRceChartCard extends HTMLElement {
  constructor() {
    super();
    this._renderKey = "";
    this._states = {};
    this._language = document.documentElement.lang || "en";
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("entity is required");
    }
    this._config = config;
    this._renderKey = "";
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._states = hass.states || {};
    this._language = hass.language || this._language;
    this._update();
  }

  _update() {
    if (!this._config) return;
    const ids = [
      this._config.entity,
      this._config.plan_entity,
      this._config.current_price_entity,
      this._config.active_entity,
      this._config.block_enabled_entity,
      this._config.block_start_entity,
      this._config.block_end_entity,
    ].filter(Boolean);
    const key = `${this._language}|${ids
      .map((id) => {
        const entity = this._states[id];
        return entity ? `${entity.state}:${entity.last_updated}` : "missing";
      })
      .join("|")}`;

    if (key !== this._renderKey) {
      this._renderKey = key;
      this._render();
    }
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return {
      columns: 12,
      rows: 7,
      min_columns: 6,
      min_rows: 5,
    };
  }

  static getStubConfig() {
    return {
      entity: "sensor.hoymiles_rce_day",
      plan_entity: "sensor.hoymiles_hit_rce_optimized_plan",
      current_price_entity: "sensor.hoymiles_rce_current_price",
      active_entity: "input_boolean.hoymiles_rce_discharge_active",
      block_enabled_entity: "input_boolean.hoymiles_sale_block_enabled",
      block_start_entity: "input_datetime.hoymiles_sale_block_start",
      block_end_entity: "input_datetime.hoymiles_sale_block_end",
    };
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _number(value, digits = 3) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return new Intl.NumberFormat(this._language || "en", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(number);
  }

  _strings() {
    const polish = String(this._language || "").toLowerCase().startsWith("pl");
    return polish
      ? {
          defaultTitle: "RCE — ceny na dziś",
          noData: "Brak kompletnych danych PSE",
          noDataHint: "Automatyka nie uruchomi rozładowania bez aktualnego planu.",
          futureNoData: "Dane PSE na jutro nie są jeszcze opublikowane",
          futureNoDataHint:
            "Automatyka realizuje plan dzisiejszy i przeliczy go automatycznie po publikacji danych.",
          current: "Bieżąca",
          threshold: "Cena graniczna planu",
          exportLockout: "Blokada sprzedaży",
          disabled: "wyłączona",
          periods15: "okresów po 15 min",
          blocks30: "bloków sterowania po 30 min",
          average30: "Średnia bloku 30 min",
          lockoutHint: "Blokada sprzedaży",
          planned: "Zaplanowane rozładowanie",
          selfUse: "Autokonsumpcja",
          thresholdShort: "plan od",
          thresholdOutside: "Cena graniczna planu jest poza zakresem dzisiejszych cen",
          chartLabel: "Wykres cen RCE dla",
          belowThreshold: "Poza planem sprzedaży",
          plannedDischarge: "Planowane rozładowanie",
          currentQuarter: "Aktualny kwadrans",
          userThreshold: "Automatyczna cena graniczna",
          minimum: "Min.",
          maximum: "Maks.",
          plan: "Plan:",
          expectedExport: "Prognozowany eksport:",
          expectedRevenue: "Szacunkowy przychód:",
          active: "● RCE rozładowuje",
          inactive: "○ RCE nieaktywne",
        }
      : {
          defaultTitle: "RCE — today's prices",
          noData: "No complete PSE data",
          noDataHint: "The automation will not discharge without a current plan.",
          futureNoData: "Tomorrow's PSE data has not been published yet",
          futureNoDataHint:
            "The automation is following today's plan and will recalculate automatically after publication.",
          current: "Current",
          threshold: "Automatic plan floor",
          exportLockout: "Export lockout",
          disabled: "disabled",
          periods15: "15-minute periods",
          blocks30: "30-minute control blocks",
          average30: "30-minute block average",
          lockoutHint: "Export lockout",
          planned: "Planned discharge",
          selfUse: "Self-Use",
          thresholdShort: "plan from",
          thresholdOutside: "The automatic plan floor is outside today's price range",
          chartLabel: "RCE price chart for",
          belowThreshold: "Outside export plan",
          plannedDischarge: "Planned discharge",
          currentQuarter: "Current quarter-hour",
          userThreshold: "Automatic plan floor",
          minimum: "Min.",
          maximum: "Max.",
          plan: "Plan:",
          expectedExport: "Forecast export:",
          expectedRevenue: "Estimated revenue:",
          active: "● RCE discharging",
          inactive: "○ RCE inactive",
        };
  }

  _t(key) {
    return this._strings()[key] || key;
  }

  _timeMinutes(value, fallback) {
    const match = String(value ?? "").match(/^(\d{1,2}):(\d{2})/);
    if (!match) return fallback;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return fallback;
    return hour * 60 + minute;
  }

  _render() {
    const source = this._states[this._config.entity];
    const planState = this._config.plan_entity
      ? this._states[this._config.plan_entity]
      : undefined;
    const currentState = this._config.current_price_entity
      ? this._states[this._config.current_price_entity]
      : undefined;
    const activeState = this._config.active_entity
      ? this._states[this._config.active_entity]
      : undefined;
    const blockEnabledState = this._config.block_enabled_entity
      ? this._states[this._config.block_enabled_entity]
      : undefined;
    const blockStartState = this._config.block_start_entity
      ? this._states[this._config.block_start_entity]
      : undefined;
    const blockEndState = this._config.block_end_entity
      ? this._states[this._config.block_end_entity]
      : undefined;

    const rows = Array.isArray(source?.attributes?.value)
      ? source.attributes.value
      : [];
    const points = rows
      .map((row, index) => ({
        index,
        period: String(row.period ?? ""),
        date: String(row.business_date ?? ""),
        price: Number(row.rce_pln) / 1000,
      }))
      .filter((point) => Number.isFinite(point.price));

    const title = this._escape(this._config.title || this._t("defaultTitle"));
    if (points.length < 2) {
      const futureData =
        this._config.future_data ||
        String(this._config.entity).endsWith("_tomorrow");
      const noData = futureData
        ? this._t("futureNoData")
        : this._t("noData");
      const noDataHint = futureData
        ? this._t("futureNoDataHint")
        : this._t("noDataHint");
      this._setContent(`
        <ha-card>
          <div class="header">${title}</div>
          <div class="empty">
            <ha-icon icon="mdi:cloud-alert"></ha-icon>
            <div>
              <strong>${noData}</strong>
              <span>${noDataHint}</span>
            </div>
          </div>
        </ha-card>
      `);
      return;
    }

    const currentPrice = Number(currentState?.state);
    const automationActive = activeState?.state === "on";
    const blockEnabled = blockEnabledState?.state === "on";
    const blockStart = this._timeMinutes(blockStartState?.state, 22 * 60);
    const blockEnd = this._timeMinutes(blockEndState?.state, 6 * 60);
    const isBlocked = (minute) =>
      blockEnabled &&
      blockStart !== blockEnd &&
      (blockStart < blockEnd
        ? minute >= blockStart && minute < blockEnd
        : minute >= blockStart || minute < blockEnd);
    const blockWindow = `${String(Math.floor(blockStart / 60)).padStart(2, "0")}:${String(
      blockStart % 60,
    ).padStart(2, "0")}–${String(Math.floor(blockEnd / 60)).padStart(2, "0")}:${String(
      blockEnd % 60,
    ).padStart(2, "0")}`;
    const date = points[0].date;
    const prices = points.map((point) => point.price);
    const minimum = Math.min(...prices);
    const maximum = Math.max(...prices);

    const halfHours = [];
    for (let index = 0; index + 1 < points.length; index += 2) {
      halfHours.push((points[index].price + points[index + 1].price) / 2);
    }
    const optimizedSlots = Array.isArray(planState?.attributes?.planned_slots)
      ? planState.attributes.planned_slots
      : [];
    const optimizedForDay = optimizedSlots.filter(
      (slot) => String(slot?.date ?? "") === date,
    );
    const optimizedPrices = optimizedForDay
      .map((slot) => Number(slot?.price))
      .filter(Number.isFinite);
    const threshold = optimizedPrices.length
      ? Math.min(...optimizedPrices)
      : Number.NaN;
    const optimizedStarts = new Set(
      optimizedForDay.map((slot) => String(slot?.start ?? "").slice(0, 5)),
    );
    const hasOptimizedPlan = Boolean(this._config.plan_entity && planState);
    const plannedBlocks = hasOptimizedPlan
      ? optimizedStarts.size
      : Number.isFinite(threshold)
        ? halfHours.filter(
            (price, index) => price > threshold && !isBlocked(index * 30),
          ).length
        : 0;
    const expectedExport = optimizedForDay.reduce(
      (total, slot) => total + (Number(slot?.energy) || 0),
      0,
    );
    const expectedRevenue = optimizedForDay.reduce(
      (total, slot) => total + (Number(slot?.revenue) || 0),
      0,
    );

    const now = new Date();
    const localDate = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
    ].join("-");
    const currentQuarter =
      date === localDate ? now.getHours() * 4 + Math.floor(now.getMinutes() / 15) : -1;

    const width = 1000;
    const height = 430;
    const left = 70;
    const right = 24;
    const top = 22;
    const bottom = 65;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;

    let domainMin = Math.min(0, minimum);
    let domainMax = Math.max(0, maximum);
    const rawSpan = Math.max(0.05, domainMax - domainMin);
    domainMin -= rawSpan * 0.08;
    domainMax += rawSpan * 0.08;
    const domainSpan = domainMax - domainMin;
    const y = (value) => top + ((domainMax - value) / domainSpan) * plotHeight;
    const zeroY = y(0);

    const horizontalGrid = [];
    const yTickCount = 6;
    for (let index = 0; index <= yTickCount; index += 1) {
      const value = domainMax - (domainSpan * index) / yTickCount;
      const position = y(value);
      horizontalGrid.push(`
        <line class="grid-line" x1="${left}" y1="${position}" x2="${width - right}" y2="${position}" />
        <text class="axis-label y-label" x="${left - 10}" y="${position + 4}">${this._number(value, 2)}</text>
      `);
    }

    const verticalGrid = [];
    for (let hour = 0; hour <= 24; hour += 3) {
      const position = left + (plotWidth * hour) / 24;
      const anchor = hour === 0 ? "start" : hour === 24 ? "end" : "middle";
      verticalGrid.push(`
        <line class="grid-line vertical" x1="${position}" y1="${top}" x2="${position}" y2="${top + plotHeight}" />
        <text class="axis-label x-label" text-anchor="${anchor}" x="${position}" y="${top + plotHeight + 28}">
          ${String(hour).padStart(2, "0")}:00
        </text>
      `);
    }

    const slotWidth = plotWidth / points.length;
    const bars = points.map((point, index) => {
      const pair = Math.floor(index / 2);
      const blocked = isBlocked(pair * 30);
      const pairStart = String(points[pair * 2]?.period ?? "")
        .split(" - ")[0]
        .slice(0, 5);
      const planned = hasOptimizedPlan
        ? optimizedStarts.has(pairStart)
        : !blocked && Number.isFinite(threshold) && halfHours[pair] > threshold;
      const barX = left + index * slotWidth + slotWidth * 0.09;
      const valueY = y(point.price);
      const barY = Math.min(valueY, zeroY);
      const barHeight = Math.max(1.5, Math.abs(zeroY - valueY));
      const current = index === currentQuarter;
      const classes = [
        "bar",
        blocked ? "blocked" : planned ? "planned" : "normal",
        point.price < 0 ? "negative" : "",
        current ? "current" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const pairAverage = halfHours[pair];
      return `
        <rect class="${classes}" x="${barX}" y="${barY}"
              width="${Math.max(1.5, slotWidth * 0.82)}" height="${barHeight}" rx="1">
          <title>${this._escape(point.period)}: ${this._number(point.price, 4)} PLN/kWh
${this._t("average30")}: ${this._number(pairAverage, 4)} PLN/kWh
${blocked ? `${this._t("lockoutHint")} ${blockWindow} — Self-Use` : planned ? this._t("planned") : this._t("selfUse")}</title>
        </rect>
      `;
    });

    const thresholdVisible =
      Number.isFinite(threshold) && threshold >= domainMin && threshold <= domainMax;
    const thresholdLine = thresholdVisible
      ? `
        <line class="threshold-line" x1="${left}" y1="${y(threshold)}"
              x2="${width - right}" y2="${y(threshold)}" />
        <rect class="threshold-label-bg" x="${width - right - 116}" y="${y(threshold) - 20}"
              width="112" height="18" rx="5" />
        <text class="threshold-label" text-anchor="end" x="${width - right - 8}"
              y="${y(threshold) - 7}">
          ${this._t("thresholdShort")} ${this._number(threshold, 2)}
        </text>
      `
      : "";

    const thresholdHint = !thresholdVisible && Number.isFinite(threshold)
      ? `<span class="range-note">${this._t("thresholdOutside")}</span>`
      : "";

    this._setContent(`
      <ha-card>
        <div class="top">
          <div>
            <div class="header">${title}</div>
            <div class="subheader">${this._escape(date)} • ${points.length} ${this._t("periods15")} • ${halfHours.length} ${this._t("blocks30")}</div>
          </div>
          <div class="badges">
            ${
              date === localDate
                ? `<div class="badge">
              <span>${this._t("current")}</span>
              <strong>${this._number(currentPrice, 4)} PLN/kWh</strong>
            </div>`
                : ""
            }
            <div class="badge threshold">
              <span>${this._t("threshold")}</span>
              <strong>${this._number(threshold, 2)} PLN/kWh</strong>
            </div>
            <div class="badge block ${blockEnabled ? "enabled" : ""}">
              <span>${this._t("exportLockout")}</span>
              <strong>${blockEnabled ? blockWindow : this._t("disabled")}</strong>
            </div>
          </div>
        </div>

        <div class="chart-wrap">
          <svg viewBox="0 0 ${width} ${height}" role="img"
               aria-label="${this._t("chartLabel")} ${this._escape(date)}">
            <text class="axis-title" transform="rotate(-90)" text-anchor="middle"
                  x="${-(top + plotHeight / 2)}" y="18">PLN/kWh</text>
            ${horizontalGrid.join("")}
            ${verticalGrid.join("")}
            <line class="zero-line" x1="${left}" y1="${zeroY}"
                  x2="${width - right}" y2="${zeroY}" />
            ${bars.join("")}
            ${thresholdLine}
          </svg>
        </div>

        <div class="legend">
          <span><i class="swatch normal"></i>${this._t("belowThreshold")}</span>
          <span><i class="swatch planned"></i>${this._t("plannedDischarge")}</span>
          <span><i class="swatch blocked"></i>${this._t("exportLockout")}</span>
          <span><i class="swatch current"></i>${this._t("currentQuarter")}</span>
          <span><i class="line"></i>${this._t("userThreshold")}</span>
        </div>

        <div class="footer">
          <span>${this._t("minimum")} <strong>${this._number(minimum, 3)}</strong></span>
          <span>${this._t("maximum")} <strong>${this._number(maximum, 3)}</strong></span>
          <span>${this._t("plan")} <strong>${plannedBlocks} × 30 min</strong></span>
          <span>${this._t("expectedExport")} <strong>${this._number(expectedExport, 2)} kWh</strong></span>
          <span>${this._t("expectedRevenue")} <strong>${this._number(expectedRevenue, 2)} PLN</strong></span>
          <span class="${automationActive ? "active" : "inactive"}">
            ${automationActive ? this._t("active") : this._t("inactive")}
          </span>
          ${thresholdHint}
        </div>
      </ha-card>
    `);
  }

  _setContent(content) {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ${HOYMILES_AURORA_THEME_CSS}
        :host { --hoymiles-aurora-accent: ${hoymilesAuroraAccent("grid")}; }
        ha-card {
          background: var(--hoymiles-aurora-surface);
          border: 1px solid var(--hoymiles-aurora-border);
          border-radius: var(--ha-card-border-radius);
          box-shadow: var(--hoymiles-aurora-shadow);
          overflow: hidden;
          padding: 18px 18px 14px;
        }
        .top {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 10px;
        }
        .header {
          color: var(--primary-text-color);
          font-size: 22px;
          font-weight: 500;
          line-height: 1.25;
        }
        .subheader {
          color: var(--secondary-text-color);
          font-size: 12px;
          margin-top: 4px;
        }
        .badges {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 8px;
        }
        .badge {
          background: color-mix(in srgb, var(--hoymiles-aurora-accent) 15%, var(--card-background-color));
          border: 1px solid color-mix(in srgb, var(--hoymiles-aurora-accent) 42%, transparent);
          border-radius: 9px;
          min-width: 112px;
          padding: 6px 9px;
        }
        .badge.threshold {
          background: color-mix(in srgb, var(--hoymiles-aurora-warn) 14%, var(--card-background-color));
          border-color: color-mix(in srgb, var(--hoymiles-aurora-warn) 42%, transparent);
        }
        .badge.block.enabled {
          background: color-mix(in srgb, #8d8d8d 18%, var(--card-background-color));
          border-color: color-mix(in srgb, #8d8d8d 48%, transparent);
        }
        .badge span {
          color: var(--secondary-text-color);
          display: block;
          font-size: 10px;
          text-transform: uppercase;
        }
        .badge strong {
          color: var(--primary-text-color);
          display: block;
          font-size: 13px;
          margin-top: 2px;
          white-space: nowrap;
        }
        .chart-wrap {
          overflow-x: auto;
          width: 100%;
        }
        svg {
          display: block;
          min-width: 620px;
          width: 100%;
        }
        .grid-line {
          stroke: var(--divider-color);
          stroke-width: 1;
          opacity: 0.62;
        }
        .grid-line.vertical {
          opacity: 0.4;
        }
        .zero-line {
          stroke: var(--secondary-text-color);
          stroke-width: 1.4;
          opacity: 0.8;
        }
        .axis-label,
        .axis-title {
          fill: var(--secondary-text-color);
          font-family: Roboto, sans-serif;
          font-size: 13px;
        }
        .axis-title {
          font-size: 14px;
          font-weight: 500;
        }
        .bar {
          transition: opacity 120ms ease;
        }
        .bar:hover {
          opacity: 0.72;
        }
        .bar.normal {
          fill: var(--hoymiles-aurora-accent);
        }
        .bar.planned {
          fill: var(--hoymiles-aurora-good);
        }
        .bar.negative {
          fill: var(--hoymiles-aurora-error);
        }
        .bar.blocked {
          fill: #777b82;
          opacity: 0.78;
        }
        .bar.current {
          stroke: #ffd54f;
          stroke-width: 3;
          paint-order: stroke;
        }
        .threshold-line {
          stroke: var(--hoymiles-aurora-warn);
          stroke-dasharray: 8 5;
          stroke-width: 2.5;
        }
        .threshold-label-bg {
          fill: color-mix(in srgb, #ff9800 85%, #000);
        }
        .threshold-label {
          fill: #fff;
          font-family: Roboto, sans-serif;
          font-size: 12px;
          font-weight: 600;
        }
        .legend,
        .footer {
          color: var(--secondary-text-color);
          display: flex;
          flex-wrap: wrap;
          gap: 8px 18px;
          font-size: 12px;
          margin-top: 8px;
        }
        .legend span,
        .footer span {
          align-items: center;
          display: inline-flex;
          gap: 6px;
        }
        .footer {
          border-top: 1px solid var(--divider-color);
          padding-top: 10px;
        }
        .footer strong {
          color: var(--primary-text-color);
        }
        .swatch {
          border-radius: 2px;
          display: inline-block;
          height: 10px;
          width: 16px;
        }
        .swatch.normal { background: var(--hoymiles-aurora-accent); }
        .swatch.planned { background: var(--hoymiles-aurora-good); }
        .swatch.blocked { background: #777b82; }
        .swatch.current {
          background: transparent;
          border: 2px solid #ffd54f;
          box-sizing: border-box;
        }
        .line {
          border-top: 2px dashed var(--hoymiles-aurora-warn);
          display: inline-block;
          width: 18px;
        }
        .active { color: var(--hoymiles-aurora-good); font-weight: 600; }
        .inactive { color: var(--secondary-text-color); }
        .range-note { color: var(--hoymiles-aurora-warn); }
        .empty {
          align-items: center;
          color: var(--secondary-text-color);
          display: flex;
          gap: 14px;
          padding: 28px 4px 18px;
        }
        .empty ha-icon {
          color: var(--hoymiles-aurora-warn);
          --mdc-icon-size: 36px;
        }
        .empty strong,
        .empty span {
          display: block;
        }
        .empty strong {
          color: var(--primary-text-color);
          margin-bottom: 4px;
        }
        @media (max-width: 620px) {
          ha-card { padding: 14px 12px 12px; }
          .top { display: block; }
          .badges { justify-content: flex-start; margin-top: 10px; }
          .badge { flex: 1 1 120px; }
          .header { font-size: 20px; }
        }
      </style>
      ${content}
    `;
  }
}

if (!customElements.get("hoymiles-rce-chart-card")) {
  customElements.define("hoymiles-rce-chart-card", HoymilesRceChartCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "hoymiles-rce-chart-card")) {
  window.customCards.push({
    type: "hoymiles-rce-chart-card",
    name: "Hoymiles RCE Chart",
    description: "Readable chart of 96 RCE prices with a 48-block EMS plan.",
    preview: true,
  });
}

class HoymilesDiagnosticsDownloadCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._busy = false;
    this._status = "";
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    const previousLanguage = this._language;
    this._hass = hass;
    this._language = String(hass?.language || "en").toLowerCase();
    if (previousLanguage !== this._language || !this.shadowRoot?.childElementCount) {
      this._render();
    }
  }

  connectedCallback() {
    this._render();
  }

  _translations() {
    if (this._language?.startsWith("pl")) {
      return {
        title: "Pakiet diagnostyczny",
        description:
          "Jednym kliknięciem zbierz stan integracji, historię sterowania z 24 godzin i odfiltrowane logi Home Assistanta.",
        privacy:
          "Sekrety i identyfikatory instalacji są maskowane. Przejrzyj ZIP przed publicznym udostępnieniem.",
        contact:
          "W razie błędu pobierz ZIP i wyślij go wraz z opisem problemu oraz dokładną datą i godziną wystąpienia na:",
        button: "Zbierz dane i pobierz ZIP",
        preparing: "Przygotowywanie pakietu…",
        downloaded: "Pakiet został pobrany.",
        adminOnly: "Pobranie pakietu wymaga konta administratora Home Assistanta.",
        error: "Nie udało się utworzyć pakietu. Sprawdź uprawnienia administratora i log HA.",
      };
    }
    return {
      title: "Diagnostic package",
      description:
        "Collect integration state, 24 hours of control history and filtered Home Assistant logs with one click.",
      privacy:
        "Secrets and installation identifiers are masked. Review the ZIP before sharing it publicly.",
      contact:
        "If an error occurs, download the ZIP and email it with a problem description and the exact date and time to:",
      button: "Collect data and download ZIP",
      preparing: "Preparing package…",
      downloaded: "The package has been downloaded.",
      adminOnly: "Downloading the package requires a Home Assistant administrator account.",
      error:
        "The package could not be created. Check administrator access and the HA log.",
    };
  }

  async _download() {
    if (this._busy || !this._hass) return;
    const text = this._translations();
    if (!this._hass.user?.is_admin) {
      this._status = text.adminOnly;
      this._updateAction();
      return;
    }
    this._busy = true;
    this._status = text.preparing;
    this._updateAction();
    try {
      const response = await this._hass.fetchWithAuth(
        "/api/hoymiles_hit_modbus/support-bundle",
        {
          method: "GET",
          cache: "no-store",
        }
      );
      if (!response.ok) {
        throw new Error(`Diagnostic download failed (${response.status})`);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^";]+)"?/i);
      const filename = match?.[1] || "hoymiles_diagnostics.zip";
      const downloadUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = downloadUrl;
      anchor.download = filename;
      anchor.style.display = "none";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 30_000);
      this._status = text.downloaded;
    } catch (error) {
      console.error("Hoymiles diagnostic download failed", error);
      this._status = text.error;
    } finally {
      this._busy = false;
      this._updateAction();
    }
  }

  _updateAction() {
    const button = this.shadowRoot?.querySelector("button");
    const status = this.shadowRoot?.querySelector(".status");
    if (button) {
      button.disabled = this._busy || Boolean(
        this._hass?.user && !this._hass.user.is_admin
      );
      button.textContent = this._busy
        ? this._translations().preparing
        : this._translations().button;
    }
    if (status) {
      status.textContent = this._status;
      status.hidden = !this._status;
    }
  }

  _render() {
    if (!this.isConnected || !this._config) return;
    const text = this._translations();
    this.shadowRoot.innerHTML = `
      <ha-card>
        <div class="content">
          <div class="heading">
            <ha-icon icon="mdi:file-download-outline"></ha-icon>
            <div>
              <h2>${text.title}</h2>
              <p>${text.description}</p>
            </div>
          </div>
          <div class="privacy">
            <ha-icon icon="mdi:shield-lock-outline"></ha-icon>
            <span>${text.privacy}</span>
          </div>
          <div class="contact">
            <span>${text.contact}</span>
            <a href="mailto:info@kaluzaaa.com?subject=Hoymiles%20HIT%20-%20raport%20diagnostyczny">info@kaluzaaa.com</a>
          </div>
          <button type="button">${text.button}</button>
          <div class="status" role="status" aria-live="polite" hidden></div>
        </div>
      </ha-card>
      <style>
        ${HOYMILES_AURORA_THEME_CSS}
        :host { display: block; --hoymiles-aurora-accent: ${hoymilesAuroraAccent("cyan")}; }
        ha-card {
          background: var(--hoymiles-aurora-surface);
          border: 1px solid var(--hoymiles-aurora-border);
          border-radius: var(--ha-card-border-radius);
          box-shadow: var(--hoymiles-aurora-shadow);
          overflow: hidden;
        }
        .content { padding: 18px; }
        .heading { align-items: flex-start; display: flex; gap: 14px; }
        .heading > ha-icon {
          color: var(--hoymiles-aurora-accent);
          margin-top: 2px;
          --mdc-icon-size: 34px;
        }
        h2 { font-size: 20px; margin: 0 0 6px; }
        p { color: var(--secondary-text-color); margin: 0; }
        .privacy {
          align-items: center;
          background: color-mix(in srgb, var(--hoymiles-aurora-accent) 10%, transparent);
          border-radius: 9px;
          display: flex;
          gap: 9px;
          margin: 16px 0;
          padding: 10px 12px;
        }
        .privacy ha-icon { color: var(--hoymiles-aurora-accent); flex: 0 0 auto; }
        .contact {
          display: flex;
          flex-wrap: wrap;
          gap: 5px 8px;
          margin: 0 0 16px;
        }
        .contact a {
          color: var(--hoymiles-aurora-accent);
          font-weight: 700;
          text-decoration: none;
        }
        .contact a:hover { text-decoration: underline; }
        button {
          background: var(--hoymiles-aurora-accent);
          border: 0;
          border-radius: 8px;
          color: var(--text-primary-color, white);
          cursor: pointer;
          font: inherit;
          font-weight: 600;
          min-height: 42px;
          padding: 0 18px;
        }
        button:hover { filter: brightness(1.08); }
        button:disabled { cursor: wait; opacity: 0.65; }
        .status { margin-top: 11px; }
        @media (max-width: 520px) {
          :host { --ha-card-border-radius: 16px; }
          button { width: 100%; }
        }
      </style>
    `;
    this.shadowRoot.querySelector("button")?.addEventListener(
      "click",
      () => this._download()
    );
    this._updateAction();
  }

  getCardSize() {
    return 3;
  }
}

if (!customElements.get("hoymiles-diagnostics-download-card")) {
  customElements.define(
    "hoymiles-diagnostics-download-card",
    HoymilesDiagnosticsDownloadCard
  );
}

if (
  !window.customCards.some(
    (card) => card.type === "hoymiles-diagnostics-download-card"
  )
) {
  window.customCards.push({
    type: "hoymiles-diagnostics-download-card",
    name: "Hoymiles Diagnostic Download",
    description: "Download a privacy-filtered support ZIP.",
    preview: false,
  });
}

class HoymilesZebraEntitiesCard extends HTMLElement {
  constructor() {
    super();
    this._renderVersion = 0;
  }

  setConfig(config) {
    if (!config?.entities) {
      throw new Error("Zebra entities card requires an entities list");
    }
    this._config = { ...config };
    this._mount();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._card) {
      this._card.hass = hass;
    }
  }

  connectedCallback() {
    this._mount();
  }

  async _mount() {
    if (!this.isConnected || !this._config) return;

    const renderVersion = ++this._renderVersion;
    await customElements.whenDefined("hui-entities-card");
    if (renderVersion !== this._renderVersion) return;

    const card = document.createElement("hui-entities-card");
    card.setConfig({
      ...this._config,
      type: "entities",
    });
    if (this._hass) {
      card.hass = this._hass;
    }

    this.replaceChildren(card);
    this._card = card;
    await card.updateComplete;
    if (renderVersion !== this._renderVersion || this._card !== card) return;

    const style = document.createElement("style");
    style.dataset.hoymilesZebraRows = "";
    style.textContent = `
      ${HOYMILES_AURORA_THEME_CSS}
      :host { --hoymiles-aurora-accent: ${hoymilesAuroraAccent(this._config.accent || hoymilesAuroraTextAccent(this._config.title))}; }
      ha-card {
        background: var(--hoymiles-aurora-surface);
        border-color: var(--hoymiles-aurora-border);
        border-radius: var(--ha-card-border-radius);
        box-shadow: var(--hoymiles-aurora-shadow);
        overflow: hidden;
      }
      #states > div {
        border-radius: 8px;
        box-sizing: border-box;
        margin-left: -8px;
        margin-right: -8px;
        padding: 4px 8px;
      }
      #states > div:nth-child(odd) {
        background: transparent;
      }
      #states > div:nth-child(even) {
        background: color-mix(
          in srgb,
          var(--card-background-color, var(--ha-card-background)) 91%,
          var(--hoymiles-aurora-accent) 9%
        );
      }
      @media (max-width: 420px) {
        :host { --ha-card-border-radius: 16px; }
      }
    `;
    card.shadowRoot?.append(style);
  }

  getCardSize() {
    return this._card?.getCardSize?.() ?? this._config?.entities?.length ?? 1;
  }

  getGridOptions() {
    return this._card?.getGridOptions?.();
  }
}

if (!customElements.get("hoymiles-zebra-entities-card")) {
  customElements.define(
    "hoymiles-zebra-entities-card",
    HoymilesZebraEntitiesCard
  );
}

if (
  !window.customCards.some(
    (card) => card.type === "hoymiles-zebra-entities-card"
  )
) {
  window.customCards.push({
    type: "hoymiles-zebra-entities-card",
    name: "Hoymiles Zebra Entities",
    description: "Entities card with alternating row backgrounds.",
    preview: false,
  });
}

class HoymilesResponsiveGlanceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    if (!config?.entities) {
      throw new Error("Responsive glance card requires an entities list");
    }
    this._config = { ...config };
    this._mount();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    this._render();
  }

  _mount() {
    if (!this.isConnected || !this._config) return;
    const style = document.createElement("style");
    const minimumWidth = Math.max(Number(this._config.minimum_width) || 112, 80);
    style.textContent = `
      ${HOYMILES_AURORA_THEME_CSS}
      :host {
        container-type: inline-size;
        display: block;
        --hoymiles-aurora-accent: ${hoymilesAuroraAccent(this._config.accent || hoymilesAuroraTextAccent(this._config.title))};
      }
      ha-card {
        background: var(--hoymiles-aurora-surface);
        border: 1px solid var(--hoymiles-aurora-border);
        border-radius: var(--ha-card-border-radius);
        box-shadow: var(--hoymiles-aurora-shadow);
        overflow: hidden;
      }
      .title {
        color: var(--ha-card-header-color, var(--primary-text-color));
        font-size: var(--ha-card-header-font-size, 24px);
        line-height: 1.2;
        padding: 20px 16px 10px;
      }
      .entities {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(min(100%, ${minimumWidth}px), 1fr));
        gap: 8px;
        padding: ${this._config.title ? "6px 12px 16px" : "16px 12px"};
      }
      .entity {
        appearance: none;
        background: transparent;
        border: 0;
        border-radius: 10px;
        color: var(--primary-text-color);
        cursor: pointer;
        display: grid;
        grid-template-rows: 28px auto auto;
        justify-items: center;
        min-width: 0;
        padding: 8px 6px;
        text-align: center;
      }
      .entity:hover { background: color-mix(in srgb, var(--hoymiles-aurora-accent) 9%, transparent); }
      ha-state-icon {
        color: var(--state-icon-color);
        height: 24px;
        width: 24px;
      }
      .name {
        color: var(--secondary-text-color);
        font-size: 13px;
        line-height: 1.25;
        margin-top: 5px;
        max-width: 100%;
        overflow-wrap: anywhere;
        white-space: normal;
      }
      .state {
        font-size: 14px;
        line-height: 1.3;
        margin-top: 4px;
        max-width: 100%;
        overflow-wrap: anywhere;
        white-space: normal;
      }
      @container (max-width: 420px) {
        :host { --ha-card-border-radius: 16px; }
        .entities { gap: 5px; padding-left: 8px; padding-right: 8px; }
        .entity { padding-left: 4px; padding-right: 4px; }
      }
      @media (prefers-reduced-motion: reduce) {
        .entity { transition: none; }
      }
    `;
    this._card = document.createElement("ha-card");
    this._title = document.createElement("div");
    this._title.className = "title";
    this._title.textContent = this._config.title || "";
    this._entities = document.createElement("div");
    this._entities.className = "entities";
    this._card.append(
      ...(this._config.title ? [this._title] : []),
      this._entities
    );
    this.shadowRoot.replaceChildren(style, this._card);
    this._render();
  }

  _render() {
    if (!this.isConnected || !this._config) return;
    if (!this._entities) {
      this._mount();
      return;
    }

    const hass = this._hass;
    const rows = this._config.entities.map((entry) => {
      const entityConfig = typeof entry === "string" ? { entity: entry } : entry;
      const entityId = entityConfig.entity;
      const stateObj = hass?.states?.[entityId];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "entity";
      button.title = entityConfig.name || stateObj?.attributes?.friendly_name || entityId;
      button.addEventListener("click", () => {
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            bubbles: true,
            composed: true,
            detail: { entityId },
          })
        );
      });

      const icon = document.createElement("ha-state-icon");
      icon.hass = hass;
      icon.stateObj = stateObj;
      if (entityConfig.icon) icon.icon = entityConfig.icon;

      const name = document.createElement("div");
      name.className = "name";
      name.textContent =
        entityConfig.name || stateObj?.attributes?.friendly_name || entityId;

      const state = document.createElement("div");
      state.className = "state";
      state.textContent = stateObj
        ? hass?.formatEntityState?.(stateObj) ?? stateObj.state
        : "—";

      if (this._config.show_name === false) name.hidden = true;
      if (this._config.show_state === false) state.hidden = true;
      button.append(icon, name, state);
      return button;
    });
    this._entities.replaceChildren(...rows);
  }

  getCardSize() {
    return this._card?.getCardSize?.() ?? 2;
  }
}

if (!customElements.get("hoymiles-responsive-glance-card")) {
  customElements.define(
    "hoymiles-responsive-glance-card",
    HoymilesResponsiveGlanceCard
  );
}

class HoymilesResponsiveStackCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cards = [];
    this._renderVersion = 0;
  }

  setConfig(config) {
    if (!Array.isArray(config?.cards) || config.cards.length === 0) {
      throw new Error("Responsive stack card requires a cards list");
    }
    this._config = { ...config };
    this._mount();
  }

  set hass(hass) {
    this._hass = hass;
    for (const card of this._cards) card.hass = hass;
  }

  connectedCallback() {
    this._mount();
  }

  async _mount() {
    if (!this.isConnected || !this._config) return;
    const renderVersion = ++this._renderVersion;
    const helpers = await window.loadCardHelpers();
    if (renderVersion !== this._renderVersion) return;
    const cards = this._config.cards.map((config) =>
      helpers.createCardElement(config)
    );
    if (this._hass) {
      for (const card of cards) card.hass = this._hass;
    }
    const minimumWidth = Math.max(Number(this._config.minimum_width) || 280, 180);
    const gap = Math.max(Number(this._config.gap) || 8, 0);
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(min(100%, ${minimumWidth}px), 1fr));
        gap: ${gap}px;
        align-items: start;
      }
    `;
    const grid = document.createElement("div");
    grid.className = "grid";
    grid.append(...cards);
    this.shadowRoot.replaceChildren(style, grid);
    this._cards = cards;
  }

  getCardSize() {
    return Math.max(...this._cards.map((card) => card.getCardSize?.() ?? 1), 1);
  }
}

if (!customElements.get("hoymiles-responsive-stack-card")) {
  customElements.define(
    "hoymiles-responsive-stack-card",
    HoymilesResponsiveStackCard
  );
}

for (const card of [
  {
    type: "hoymiles-responsive-glance-card",
    name: "Hoymiles Responsive Glance",
    description: "Glance card that wraps cleanly on phones.",
  },
  {
    type: "hoymiles-responsive-stack-card",
    name: "Hoymiles Responsive Stack",
    description: "Multi-card grid that becomes one column on narrow screens.",
  },
]) {
  if (!window.customCards.some((item) => item.type === card.type)) {
    window.customCards.push({ ...card, preview: false });
  }
}

class HoymilesAuroraFrameCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._card = null;
    this._renderVersion = 0;
  }

  setConfig(config) {
    if (!config?.card || typeof config.card !== "object") {
      throw new Error("Aurora frame card requires a nested card configuration");
    }
    this._config = {
      ...config,
      accent: Object.hasOwn(HOYMILES_AURORA_ACCENTS, config.accent)
        ? config.accent
        : "neutral",
      card: { ...config.card },
    };
    this._mount();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._card) this._card.hass = hass;
  }

  connectedCallback() {
    this._mount();
  }

  disconnectedCallback() {
    this._renderVersion += 1;
  }

  async _mount() {
    if (!this.isConnected || !this._config) return;
    const renderVersion = ++this._renderVersion;
    const helpers = await window.loadCardHelpers();
    if (renderVersion !== this._renderVersion || !this.isConnected) return;

    const card = helpers.createCardElement({ ...this._config.card });
    if (this._hass) card.hass = this._hass;
    const accent = hoymilesAuroraAccent(this._config.accent);
    const style = document.createElement("style");
    style.textContent = `
      ${HOYMILES_AURORA_THEME_CSS}
      :host {
        container-type: inline-size;
        display: block;
        --hoymiles-aurora-accent: ${accent};
      }
      .surface {
        border-radius: var(--ha-card-border-radius, 20px);
        isolation: isolate;
        position: relative;
      }
      .surface::after {
        border: 1px solid var(--hoymiles-aurora-border);
        border-radius: inherit;
        box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 4%, transparent);
        content: "";
        inset: 0;
        pointer-events: none;
        position: absolute;
        z-index: 2;
      }
      .surface > * {
        position: relative;
        z-index: 1;
      }
      @container (max-width: 420px) {
        :host {
          --ha-card-border-radius: 16px;
          --hoymiles-aurora-shadow: 0 8px 22px color-mix(in srgb, #000 14%, transparent);
        }
        .surface::after {
          box-shadow: none;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .surface::after { transition: none; }
      }
    `;
    const surface = document.createElement("div");
    surface.className = "surface";
    surface.append(card);
    this.shadowRoot.replaceChildren(style, surface);
    this._card = card;
  }

  getCardSize() {
    return this._card?.getCardSize?.() ?? 1;
  }

  getGridOptions() {
    return this._card?.getGridOptions?.();
  }
}

if (!customElements.get("hoymiles-aurora-frame-card")) {
  customElements.define("hoymiles-aurora-frame-card", HoymilesAuroraFrameCard);
}

if (!window.customCards.some((card) => card.type === "hoymiles-aurora-frame-card")) {
  window.customCards.push({
    type: "hoymiles-aurora-frame-card",
    name: "Hoymiles Aurora Frame",
    description: "Theme-aware Aurora surface around a native Home Assistant card.",
    preview: false,
  });
}

class HoymilesAuroraStatusCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._renderKey = "";
  }

  setConfig(config) {
    this._config = {
      system_entity: "sensor.hoymiles_hit_overview_system_work_status",
      inverter_entity: "sensor.hoymiles_hit_inverter_work_status",
      meter_entity: "sensor.hoymiles_hit_meter_link_status",
      battery_entity: "sensor.hoymiles_hit_battery_link_status",
      parallel_entity: "sensor.hoymiles_hit_parallel_ems_control_status",
      setup_entity: null,
      alarm_entities: [],
      details_path: "stany-alarmy",
      ...config,
    };
    if (!Array.isArray(this._config.alarm_entities)) {
      throw new Error("Aurora status card alarm_entities must be a list");
    }
    this._renderKey = "";
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  connectedCallback() {
    this._update();
  }

  _copy() {
    const pl = hoymilesLanguage(this._hass, this._config?.language) === "pl";
    return pl
      ? {
          title: "Stan instalacji",
          allGood: "System działa prawidłowo",
          warning: "System wymaga uwagi",
          error: "Wykryto błąd systemu",
          unavailable: "Część danych jest niedostępna",
          system: "System",
          inverter: "Falownik",
          meter: "Licznik",
          battery: "Bateria",
          parallel: "Sieć równoległa",
          setup: "Integracja",
          alarms: "Alarmy",
          details: "Stany i alarmy",
          changed: "zmiana",
          now: "przed chwilą",
          minute: "min temu",
          hour: "godz. temu",
          day: "dni temu",
          noData: "Brak danych",
          noAlarm: "Brak aktywnych alarmów",
        }
      : {
          title: "Installation status",
          allGood: "System operating normally",
          warning: "System needs attention",
          error: "System fault detected",
          unavailable: "Some data is unavailable",
          system: "System",
          inverter: "Inverter",
          meter: "Meter",
          battery: "Battery",
          parallel: "Parallel network",
          setup: "Integration",
          alarms: "Alarms",
          details: "States and alarms",
          changed: "changed",
          now: "just now",
          minute: "min ago",
          hour: "h ago",
          day: "days ago",
          noData: "No data",
          noAlarm: "No active alarms",
        };
  }

  _alarmEntityId(entry) {
    return typeof entry === "string" ? entry : entry?.entity;
  }

  _entityRows() {
    const copy = this._copy();
    const rows = [
      ["system", copy.system, this._config.system_entity, false],
      ["inverter", copy.inverter, this._config.inverter_entity, false],
      ["meter", copy.meter, this._config.meter_entity, false],
      ["battery", copy.battery, this._config.battery_entity, false],
      ["parallel", copy.parallel, this._config.parallel_entity, false],
    ];
    if (this._config.setup_entity) {
      rows.push(["setup", copy.setup, this._config.setup_entity, false]);
    }
    for (const entry of this._config.alarm_entities) {
      const entityId = this._alarmEntityId(entry);
      if (!entityId) continue;
      const state = this._hass?.states?.[entityId];
      rows.push([
        "alarm",
        typeof entry === "object" && entry?.name
          ? entry.name
          : state?.attributes?.friendly_name || copy.alarms,
        entityId,
        true,
      ]);
    }
    return rows;
  }

  _toneForState(state, alarm = false) {
    if (alarm) {
      if (!state) return "offline";
      const value = String(state.state).trim().toLowerCase();
      if (["unknown", "unavailable", ""].includes(value) || /niedostępn|niedostepn/.test(value)) return "offline";
      if (/^(0|0\.0|ok|normal|none|no_error|no_errors)$/.test(value)) return "good";
      if (/brak (błęd|bled|alarm)|no[ _-]?(fault|error|errors|alarm)|bez błęd/.test(value)) return "good";
      return "error";
    }
    if (!state || ["unknown", "unavailable", ""].includes(String(state.state).toLowerCase()) || /niedostępn|niedostepn/.test(String(state.state).toLowerCase())) {
      return "offline";
    }
    const value = String(state.state).trim().toLowerCase();
    if (/błąd|blad|fault|error|alarm|awari|offline|rozłącz|rozlacz|disconnected|niegotow|not ready/.test(value)) {
      return "error";
    }
    if (/ostrzeż|ostrzez|warning|wyspow|off.?grid|brak sieci|no grid|oczek|partial|część|czesc|degraded/.test(value)) {
      return "warn";
    }
    if (/^ok$|online|gotow|ready|normal|running|operating|pracuj|praca|grid.?connected|connected|połącz|polacz|self.?use|autokonsump|ładow|ladow|rozładow|rozladow|czuwan|idle|standby/.test(value)) {
      return "good";
    }
    return "warn";
  }

  _relativeTime(value) {
    const copy = this._copy();
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp)) return copy.noData;
    const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
    if (seconds < 60) return copy.now;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} ${copy.minute}`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} ${copy.hour}`;
    return `${Math.round(hours / 24)} ${copy.day}`;
  }

  _detailsPath() {
    const configured = String(this._config?.details_path || "stany-alarmy").trim();
    if (!configured) return "";
    if (configured.startsWith("/")) return configured;
    const first = String(globalThis.location?.pathname || "")
      .split("/")
      .filter(Boolean)[0];
    return `/${first || "hoymiles-falownik"}/${configured.replace(/^\/+/, "")}`;
  }

  _navigate(event) {
    event?.preventDefault?.();
    const path = this._detailsPath();
    if (!path || !globalThis.history?.pushState) return;
    globalThis.history.pushState(null, "", path);
    globalThis.window?.dispatchEvent?.(new Event("location-changed"));
  }

  _update() {
    if (!this._config || !this.isConnected) return;
    const rows = this._entityRows();
    const key = `${hoymilesLanguage(this._hass, this._config.language)}|${rows
      .map(([, , entityId]) => {
        const state = this._hass?.states?.[entityId];
        return `${entityId}:${state?.state || "missing"}:${state?.last_changed || ""}`;
      })
      .join("|")}`;
    if (key === this._renderKey) return;
    this._renderKey = key;
    this._render(rows);
  }

  _render(rows) {
    const copy = this._copy();
    const evaluated = rows.map(([key, label, entityId, alarm]) => {
      const state = this._hass?.states?.[entityId];
      return { key, label, entityId, state, tone: this._toneForState(state, alarm), alarm };
    });
    const alarmRows = evaluated.filter((row) => row.alarm);
    const healthRows = evaluated.filter((row) => !row.alarm || row.tone !== "offline");
    const errors = healthRows.filter((row) => row.tone === "error");
    const warnings = healthRows.filter((row) => row.tone === "warn");
    const offline = healthRows.filter((row) => row.tone === "offline");
    const summaryTone = errors.length ? "error" : warnings.length || offline.length ? "warn" : "good";
    const summary = errors.length
      ? copy.error
      : offline.length
        ? copy.unavailable
        : warnings.length
          ? copy.warning
          : copy.allGood;
    const statusRows = evaluated.filter((row) => !row.alarm);
    const activeAlarms = alarmRows.filter((row) => row.tone === "error" || row.tone === "warn");
    const compact = summaryTone === "good";
    const problemRows = healthRows.filter((row) => row.tone !== "good");
    this._summaryTone = summaryTone;

    this.shadowRoot.innerHTML = `
      <style>
        ${HOYMILES_AURORA_THEME_CSS}
        :host { container-type: inline-size; display: block; --hoymiles-aurora-accent: ${hoymilesAuroraAccent(summaryTone === "error" ? "load" : summaryTone === "warn" ? "warning" : "cyan")}; }
        * { box-sizing: border-box; }
        button, a { font: inherit; }
        ha-card { background: var(--hoymiles-aurora-surface); border: 1px solid var(--hoymiles-aurora-border); border-radius: var(--ha-card-border-radius); box-shadow: var(--hoymiles-aurora-shadow); color: var(--hoymiles-aurora-text); overflow: hidden; padding: 18px; }
        .top { align-items: center; display: flex; gap: 12px; justify-content: space-between; }
        .eyebrow { color: var(--hoymiles-aurora-muted); font-size: 11px; letter-spacing: .09em; text-transform: uppercase; }
        .summary { align-items: center; display: flex; font-size: 18px; font-weight: 650; gap: 9px; margin-top: 3px; }
        .summary-dot, .dot { background: var(--tone); border-radius: 50%; box-shadow: 0 0 12px color-mix(in srgb, var(--tone) 75%, transparent); flex: 0 0 auto; height: 8px; width: 8px; }
        .summary.good, .item.good, .chip.good { --tone: var(--hoymiles-aurora-good); }
        .summary.warn, .item.warn, .chip.warn { --tone: var(--hoymiles-aurora-warn); }
        .summary.error, .item.error, .chip.error { --tone: var(--hoymiles-aurora-error); }
        .item.offline, .chip.offline { --tone: var(--hoymiles-aurora-offline); }
        .details { align-items: center; background: color-mix(in srgb, var(--hoymiles-aurora-accent) 11%, transparent); border: 1px solid color-mix(in srgb, var(--hoymiles-aurora-accent) 28%, transparent); border-radius: 999px; color: var(--hoymiles-aurora-text); cursor: pointer; display: inline-flex; min-height: 38px; padding: 7px 12px; text-decoration: none; }
        .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 11px; }
        .chip { align-items: center; background: color-mix(in srgb, var(--card-background-color) 91%, var(--hoymiles-aurora-good) 9%); border: 1px solid color-mix(in srgb, var(--hoymiles-aurora-good) 20%, var(--divider-color)); border-radius: 999px; color: inherit; cursor: pointer; display: inline-flex; gap: 6px; min-height: 30px; min-width: 0; padding: 5px 9px; }
        .chip .dot { height: 6px; width: 6px; }
        .chip-label { color: var(--hoymiles-aurora-muted); font-size: 9px; }
        .chip-value { font-size: 10px; font-weight: 600; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 176px), 1fr)); margin-top: 15px; }
        .item { align-items: center; background: color-mix(in srgb, var(--card-background-color) 88%, var(--primary-text-color) 12%); border: 1px solid color-mix(in srgb, var(--divider-color) 75%, transparent); border-radius: 13px; color: inherit; cursor: pointer; display: grid; gap: 3px; grid-template-columns: 11px minmax(0, 1fr); min-height: 78px; padding: 10px; text-align: left; }
        .item:hover, .details:hover { border-color: color-mix(in srgb, var(--tone, var(--hoymiles-aurora-accent)) 38%, transparent); }
        .label, .changed { color: var(--hoymiles-aurora-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .label { font-size: 10px; }
        .value { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .changed { font-size: 9px; }
        .copy { min-width: 0; }
        .alarms { border-top: 1px solid var(--divider-color); color: ${activeAlarms.length ? "var(--hoymiles-aurora-error)" : "var(--hoymiles-aurora-muted)"}; font-size: 11px; margin-top: 11px; padding-top: 9px; }
        @container (max-width: 700px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .item:last-child:nth-child(odd) { grid-column: 1 / -1; } }
        @container (max-width: 420px) { ha-card { border-radius: 16px; padding: 14px 12px; } .summary { font-size: 16px; } .details { min-height: 36px; padding: 6px 10px; } .chips { gap: 5px; } .chip { flex: 1 1 135px; justify-content: center; } .grid { gap: 6px; margin-top: 12px; } .item { min-height: 72px; padding: 8px; } }
        @media (prefers-reduced-motion: reduce) { .summary-dot, .dot { box-shadow: none; } }
      </style>
      <ha-card>
        <div class="top">
          <div><div class="eyebrow">${hoymilesEscape(copy.title)}</div><div class="summary ${summaryTone}"><span class="summary-dot"></span>${hoymilesEscape(summary)}</div></div>
          <button class="details" type="button">${hoymilesEscape(copy.details)} →</button>
        </div>
        ${compact
          ? `<div class="chips">${statusRows.map((row) => `<button class="chip ${row.tone}" type="button" data-entity="${hoymilesEscape(row.entityId)}"><span class="dot"></span><span class="chip-label">${hoymilesEscape(row.label)}</span><span class="chip-value">${hoymilesEscape(row.state?.state || copy.noData)}</span></button>`).join("")}</div>`
          : `<div class="grid">${problemRows.map((row) => `<button class="item ${row.tone}" type="button" data-entity="${hoymilesEscape(row.entityId)}"><span class="dot"></span><span class="copy"><span class="label">${hoymilesEscape(row.label)}</span><span class="value">${hoymilesEscape(row.state?.state || copy.noData)}</span><span class="changed">${hoymilesEscape(copy.changed)}: ${hoymilesEscape(this._relativeTime(row.state?.last_changed))}</span></span></button>`).join("")}</div>`}
        ${compact ? "" : `<div class="alarms">${activeAlarms.length ? `${hoymilesEscape(copy.alarms)}: ${activeAlarms.length}` : hoymilesEscape(copy.noAlarm)}</div>`}
      </ha-card>`;
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((button) => {
      button.addEventListener("click", () => hoymilesDispatchMoreInfo(this, button.dataset.entity));
    });
    this.shadowRoot.querySelector(".details")?.addEventListener("click", (event) => this._navigate(event));
  }

  getCardSize() { return this._summaryTone === "good" ? 2 : 4; }
  getGridOptions() { return { columns: 12, rows: 4, min_columns: 6 }; }
}

if (!customElements.get("hoymiles-aurora-status-card")) {
  customElements.define("hoymiles-aurora-status-card", HoymilesAuroraStatusCard);
}
if (!window.customCards.some((card) => card.type === "hoymiles-aurora-status-card")) {
  window.customCards.push({ type: "hoymiles-aurora-status-card", name: "Hoymiles Aurora Status", description: "Compact health, connectivity and alarm summary.", preview: false });
}

class HoymilesAuroraHistoryCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._history = new Map();
    this._requestVersion = 0;
    this._lastFetch = 0;
    this._loading = false;
    this._error = "";
    this._renderKey = "";
  }

  setConfig(config) {
    this._requestVersion += 1;
    this._loading = false;
    const defaults = [
      { entity: "sensor.hoymiles_hit_overview_pv_total_power", name: "PV", color: HOYMILES_AURORA_ACCENTS.pv },
      { entity: "sensor.hoymiles_actual_load_power", name: "Dom", name_en: "Home", color: HOYMILES_AURORA_ACCENTS.load },
      { entity: "sensor.hoymiles_hit_overview_grid_total_active_power", name: "Sieć", name_en: "Grid", color: HOYMILES_AURORA_ACCENTS.grid },
      { entity: "sensor.hoymiles_hit_overview_battery_power", name: "Bateria", name_en: "Battery", color: HOYMILES_AURORA_ACCENTS.battery },
    ];
    const entities = config?.entities || defaults;
    if (!Array.isArray(entities) || entities.length === 0) {
      throw new Error("Aurora history card requires an entities list");
    }
    this._config = {
      title: null,
      hours_to_show: 24,
      refresh_interval: 300,
      ...config,
      entities: entities.map((entry, index) => {
        const item = typeof entry === "string" ? { entity: entry } : { ...entry };
        if (!item.entity) throw new Error("Aurora history entity is missing entity id");
        const fallback = defaults[index % defaults.length];
        return {
          ...item,
          color: /^#[0-9a-f]{3,8}$/i.test(String(item.color || ""))
            ? item.color
            : fallback.color,
        };
      }),
    };
    this._history.clear();
    this._lastFetch = 0;
    this._error = "";
    this._renderKey = "";
    this._render();
    this._ensureHistory();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    this._ensureHistory();
  }

  connectedCallback() {
    this._render();
    this._ensureHistory();
  }

  disconnectedCallback() {
    this._requestVersion += 1;
    this._loading = false;
  }

  _copy() {
    return hoymilesLanguage(this._hass, this._config?.language) === "pl"
      ? {
          title: "Moc — ostatnie 24 godziny",
          subtitle: "Dane z rejestratora Home Assistant",
          loading: "Pobieranie historii…",
          noData: "Brak zapisanych danych z wybranego okresu",
          error: "Nie udało się pobrać historii",
          current: "teraz",
          ago: "temu",
        }
      : {
          title: "Power — last 24 hours",
          subtitle: "Home Assistant recorder data",
          loading: "Loading history…",
          noData: "No recorded data for the selected period",
          error: "History could not be loaded",
          current: "now",
          ago: "ago",
        };
  }

  _hours() {
    return Math.min(Math.max(Number(this._config?.hours_to_show) || 24, 1), 168);
  }

  _refreshMs() {
    return Math.min(Math.max(Number(this._config?.refresh_interval) || 300, 30), 3600) * 1000;
  }

  _powerKw(value, entityId, attributes = {}) {
    let number = Number(value);
    if (!Number.isFinite(number)) return null;
    const unit = String(
      attributes.unit_of_measurement ||
      this._hass?.states?.[entityId]?.attributes?.unit_of_measurement ||
      "W"
    ).toLowerCase();
    if (unit === "mw") number *= 1000;
    else if (unit !== "kw") number /= 1000;
    return number;
  }

  _currentValue(entityId) {
    const state = this._hass?.states?.[entityId];
    return this._powerKw(state?.state, entityId, state?.attributes);
  }

  _formatPower(value) {
    if (!Number.isFinite(value)) return "—";
    return `${new Intl.NumberFormat(hoymilesLanguage(this._hass, this._config?.language) === "pl" ? "pl-PL" : "en-GB", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value)} kW`;
  }

  _normalizeHistory(result, startTime, endTime) {
    const groups = Array.isArray(result) ? result : [];
    const normalized = new Map();
    for (const series of this._config.entities) {
      const group = groups.find((items) =>
        Array.isArray(items) && items.some((item) => item?.entity_id === series.entity)
      ) || [];
      const points = group
        .map((item) => ({
          time: new Date(item?.last_changed || item?.last_updated).getTime(),
          value: this._powerKw(item?.state, series.entity, item?.attributes || {}),
        }))
        .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value) && point.time >= startTime && point.time <= endTime)
        .sort((left, right) => left.time - right.time);
      const current = this._currentValue(series.entity);
      if (Number.isFinite(current)) points.push({ time: endTime, value: current });
      normalized.set(series.entity, this._downsample(points, 420));
    }
    return normalized;
  }

  _downsample(points, maximum) {
    if (points.length <= maximum) return points;
    const first = points[0];
    const last = points[points.length - 1];
    const bucketCount = Math.max(1, Math.floor((maximum - 2) / 2));
    const duration = Math.max(last.time - first.time, 1);
    const buckets = Array.from({ length: bucketCount }, () => []);
    for (let index = 1; index < points.length - 1; index += 1) {
      const position = Math.min(
        bucketCount - 1,
        Math.max(0, Math.floor(((points[index].time - first.time) / duration) * bucketCount))
      );
      buckets[position].push(points[index]);
    }
    const sampled = [first];
    for (const bucket of buckets) {
      if (!bucket.length) continue;
      let minimum = bucket[0];
      let maximumPoint = bucket[0];
      for (const point of bucket) {
        if (point.value < minimum.value) minimum = point;
        if (point.value > maximumPoint.value) maximumPoint = point;
      }
      const extrema = minimum === maximumPoint
        ? [minimum]
        : [minimum, maximumPoint].sort((left, right) => left.time - right.time);
      for (const point of extrema) {
        const previous = sampled[sampled.length - 1];
        if (point.time !== previous.time || point.value !== previous.value) sampled.push(point);
      }
    }
    sampled.push(last);
    return sampled;
  }

  async _ensureHistory(force = false) {
    if (!this.isConnected || !this._config || !this._hass?.callApi || this._loading) return;
    const now = Date.now();
    if (!force && this._lastFetch && now - this._lastFetch < this._refreshMs()) return;
    const version = ++this._requestVersion;
    const end = new Date(now);
    const start = new Date(now - this._hours() * 60 * 60 * 1000);
    const ids = this._config.entities.map((item) => item.entity).join(",");
    const path = `history/period/${encodeURIComponent(start.toISOString())}?filter_entity_id=${encodeURIComponent(ids)}&end_time=${encodeURIComponent(end.toISOString())}&minimal_response&no_attributes`;
    this._loading = true;
    this._error = "";
    this._render();
    try {
      const result = await this._hass.callApi("GET", path);
      if (version !== this._requestVersion || !this.isConnected) return;
      this._history = this._normalizeHistory(result, start.getTime(), end.getTime());
      this._lastFetch = Date.now();
    } catch (error) {
      if (version !== this._requestVersion || !this.isConnected) return;
      this._error = String(error?.message || error || "history_error");
    } finally {
      if (version === this._requestVersion) {
        this._loading = false;
        this._render();
      }
    }
  }

  _path(points, x, y) {
    if (!points.length) return "";
    return points.map((point, index) =>
      `${index ? "L" : "M"}${x(point.time).toFixed(2)} ${y(point.value).toFixed(2)}`
    ).join(" ");
  }

  _render() {
    if (!this._config || !this.isConnected) return;
    const renderKey = [
      hoymilesLanguage(this._hass, this._config.language),
      this._config.title || "",
      this._hours(),
      this._lastFetch,
      this._loading,
      this._error,
      ...this._config.entities.map((item) => {
        const state = this._hass?.states?.[item.entity];
        return `${item.entity}:${state?.state || "missing"}:${state?.last_updated || ""}`;
      }),
      ...this._config.entities.map((item) => `${item.entity}:${this._history.get(item.entity)?.length || 0}`),
    ].join("|");
    if (renderKey === this._renderKey) return;
    this._renderKey = renderKey;
    const copy = this._copy();
    const now = Date.now();
    const start = now - this._hours() * 60 * 60 * 1000;
    const width = 720;
    const height = 360;
    const left = 55;
    const right = 14;
    const top = 18;
    const bottom = 42;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const seriesData = this._config.entities.map((series) => ({
      ...series,
      points: (this._history.get(series.entity) || []).filter((point) => point.time >= start && point.time <= now),
      current: this._currentValue(series.entity),
    }));
    const values = seriesData.flatMap((series) => series.points.map((point) => point.value));
    let minimum = Math.min(0, ...(values.length ? values : [0]));
    let maximum = Math.max(0, ...(values.length ? values : [1]));
    if (minimum === maximum) maximum = minimum + 1;
    const padding = Math.max((maximum - minimum) * 0.08, 0.1);
    minimum -= padding;
    maximum += padding;
    const x = (time) => left + ((time - start) / Math.max(now - start, 1)) * plotWidth;
    const y = (value) => top + ((maximum - value) / (maximum - minimum)) * plotHeight;
    const zeroY = y(0);
    const yGrid = Array.from({ length: 5 }, (_, index) => {
      const value = maximum - ((maximum - minimum) * index) / 4;
      const position = y(value);
      return `<line class="grid-line" x1="${left}" y1="${position}" x2="${width - right}" y2="${position}"/><text class="axis y-label" x="${left - 8}" y="${position + 4}" text-anchor="end">${value.toFixed(1)}</text>`;
    }).join("");
    const xGrid = Array.from({ length: 5 }, (_, index) => {
      const position = left + (plotWidth * index) / 4;
      const hoursAgo = Math.round(this._hours() * (1 - index / 4));
      return `<line class="grid-line vertical" x1="${position}" y1="${top}" x2="${position}" y2="${top + plotHeight}"/><text class="axis" x="${position}" y="${height - 12}" text-anchor="${index === 0 ? "start" : index === 4 ? "end" : "middle"}">${index === 4 ? copy.current : `${hoursAgo} h`}</text>`;
    }).join("");
    const graph = seriesData.map((series, index) => {
      if (!series.points.length) return "";
      const line = this._path(series.points, x, y);
      const firstX = x(series.points[0].time).toFixed(2);
      const lastX = x(series.points[series.points.length - 1].time).toFixed(2);
      const fill = `${line} L${lastX} ${zeroY.toFixed(2)} L${firstX} ${zeroY.toFixed(2)} Z`;
      return `<defs><linearGradient id="aurora-fill-${index}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${series.color}" stop-opacity=".22"/><stop offset="1" stop-color="${series.color}" stop-opacity=".015"/></linearGradient></defs><path class="area" d="${fill}" fill="url(#aurora-fill-${index})"/><path class="series-line" d="${line}" stroke="${series.color}" style="color:${series.color}"/>`;
    }).join("");
    const title = this._config.title || copy.title;
    const stateMessage = this._loading
      ? copy.loading
      : this._error
        ? copy.error
        : values.length
          ? ""
          : copy.noData;

    this.shadowRoot.innerHTML = `
      <style>
        ${HOYMILES_AURORA_THEME_CSS}
        :host { container-type: inline-size; display: block; --hoymiles-aurora-accent: ${hoymilesAuroraAccent("cyan")}; }
        * { box-sizing: border-box; }
        button { font: inherit; }
        ha-card { background: var(--hoymiles-aurora-surface); border: 1px solid var(--hoymiles-aurora-border); border-radius: var(--ha-card-border-radius); box-shadow: var(--hoymiles-aurora-shadow); color: var(--hoymiles-aurora-text); overflow: hidden; padding: 18px 18px 13px; }
        .header { align-items: flex-start; display: flex; gap: 10px; justify-content: space-between; }
        h2 { font-size: 20px; font-weight: 600; line-height: 1.25; margin: 0; }
        .subtitle, .message { color: var(--hoymiles-aurora-muted); font-size: 11px; margin-top: 3px; }
        .period { color: var(--hoymiles-aurora-muted); font-size: 11px; white-space: nowrap; }
        .chart { height: clamp(220px, 36vw, 330px); margin-top: 8px; width: 100%; }
        svg { display: block; height: 100%; overflow: visible; width: 100%; }
        .grid-line { stroke: var(--divider-color); stroke-width: 1; opacity: .45; vector-effect: non-scaling-stroke; }
        .grid-line.vertical { opacity: .25; }
        .axis { fill: var(--hoymiles-aurora-muted); font-family: Roboto, sans-serif; font-size: 10px; }
        .series-line { fill: none; filter: drop-shadow(0 0 4px color-mix(in srgb, currentColor 24%, transparent)); stroke-linecap: round; stroke-linejoin: round; stroke-width: 2.2; vector-effect: non-scaling-stroke; }
        .area { pointer-events: none; }
        .legend { display: grid; gap: 7px; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 7px; }
        .legend button { align-items: center; background: color-mix(in srgb, var(--card-background-color) 90%, var(--primary-text-color) 10%); border: 1px solid var(--divider-color); border-radius: 11px; color: inherit; cursor: pointer; display: grid; grid-template-columns: 8px minmax(0, 1fr); min-width: 0; padding: 8px 9px; text-align: left; }
        .legend button:hover { border-color: var(--series-color); }
        .legend-dot { background: var(--series-color); border-radius: 999px; box-shadow: 0 0 9px color-mix(in srgb, var(--series-color) 65%, transparent); height: 7px; width: 7px; }
        .legend-copy { min-width: 0; }
        .legend-name { color: var(--hoymiles-aurora-muted); display: block; font-size: 9px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .legend-value { display: block; font-size: 13px; font-variant-numeric: tabular-nums; font-weight: 650; margin-top: 1px; white-space: nowrap; }
        .message { padding: 14px 2px 4px; }
        @container (max-width: 520px) { ha-card { border-radius: 16px; padding: 14px 10px 11px; } h2 { font-size: 17px; } .chart { height: 230px; } .legend { gap: 5px; grid-template-columns: repeat(2, minmax(0, 1fr)); } .legend button { padding: 7px; } .y-label { display: none; } }
        @container (max-width: 360px) { .period { display: none; } .chart { height: 215px; } }
        @media (prefers-reduced-motion: reduce) { .series-line { filter: none; } .legend-dot { box-shadow: none; } }
      </style>
      <ha-card>
        <div class="header"><div><h2>${hoymilesEscape(title)}</h2><div class="subtitle">${hoymilesEscape(copy.subtitle)}</div></div><div class="period">${this._hours()} h</div></div>
        <div class="chart"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${hoymilesEscape(title)}">${yGrid}${xGrid}<line class="grid-line" x1="${left}" y1="${zeroY}" x2="${width - right}" y2="${zeroY}"/>${graph}</svg></div>
        <div class="legend">${seriesData.map((series) => `<button type="button" data-entity="${hoymilesEscape(series.entity)}" style="--series-color:${series.color}"><span class="legend-dot"></span><span class="legend-copy"><span class="legend-name">${hoymilesEscape(hoymilesLanguage(this._hass, this._config.language) === "en" && series.name_en ? series.name_en : series.name || series.entity)}</span><span class="legend-value">${hoymilesEscape(this._formatPower(series.current))}</span></span></button>`).join("")}</div>
        ${stateMessage ? `<div class="message">${hoymilesEscape(stateMessage)}</div>` : ""}
      </ha-card>`;
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((button) => button.addEventListener("click", () => hoymilesDispatchMoreInfo(this, button.dataset.entity)));
  }

  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12, rows: 6, min_columns: 6 }; }
}

if (!customElements.get("hoymiles-aurora-history-card")) {
  customElements.define("hoymiles-aurora-history-card", HoymilesAuroraHistoryCard);
}
if (!window.customCards.some((card) => card.type === "hoymiles-aurora-history-card")) {
  window.customCards.push({ type: "hoymiles-aurora-history-card", name: "Hoymiles Aurora History", description: "Responsive 24-hour power history with native recorder data.", preview: false });
}

class HoymilesAuroraFinanceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._renderKey = "";
  }

  setConfig(config) {
    this._config = {
      daily_revenue_entity: "sensor.hoymiles_rce_revenue_daily",
      weekly_revenue_entity: "sensor.hoymiles_rce_revenue_weekly",
      monthly_revenue_entity: "sensor.hoymiles_rce_revenue_monthly",
      yearly_revenue_entity: "sensor.hoymiles_rce_revenue_yearly",
      total_revenue_entity: "sensor.hoymiles_rce_revenue_total",
      daily_export_entity: "sensor.hoymiles_rce_grid_export_energy_daily",
      weekly_export_entity: "sensor.hoymiles_rce_grid_export_energy_weekly",
      monthly_export_entity: "sensor.hoymiles_rce_grid_export_energy_monthly",
      yearly_export_entity: "sensor.hoymiles_rce_grid_export_energy_yearly",
      total_export_entity: "sensor.hoymiles_rce_grid_export_energy_total",
      current_price_entity: "sensor.hoymiles_rce_current_price",
      export_power_entity: "sensor.hoymiles_rce_grid_export_power",
      optimization_gain_entity: "sensor.hoymiles_rce_optimization_gain",
      optimization_plan_entity: "sensor.hoymiles_hit_rce_optimized_plan",
      ...config,
    };
    this._renderKey = "";
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  connectedCallback() {
    this._update();
  }

  _copy() {
    return hoymilesLanguage(this._hass, this._config?.language) === "pl"
      ? {
          eyebrow: "Wynik energetyczny",
          title: "Energia, która pracuje na Twój wynik",
          currentPrice: "Cena RCE teraz",
          exportPower: "Eksport teraz",
          gain: "Korzyść optymalizacji netto",
          day: "Dzisiaj",
          week: "Tydzień",
          month: "Miesiąc",
          year: "Rok",
          revenue: "przychodu",
          export: "wysłano",
          average: "średnio",
          lifetime: "Łącznie od uruchomienia statystyk",
          noData: "brak danych",
        }
      : {
          eyebrow: "Energy performance",
          title: "Energy working for your result",
          currentPrice: "RCE price now",
          exportPower: "Export now",
          gain: "Net optimization benefit",
          day: "Today",
          week: "Week",
          month: "Month",
          year: "Year",
          revenue: "revenue",
          export: "exported",
          average: "average",
          lifetime: "Since statistics started",
          noData: "no data",
        };
  }

  _state(entityId) {
    return entityId ? this._hass?.states?.[entityId] : undefined;
  }

  _numeric(entityId) {
    const value = Number(this._state(entityId)?.state);
    return Number.isFinite(value) ? value : null;
  }

  _format(value, digits = 2) {
    if (!Number.isFinite(value)) return "—";
    return new Intl.NumberFormat(
      hoymilesLanguage(this._hass, this._config?.language) === "pl" ? "pl-PL" : "en-GB",
      { minimumFractionDigits: digits, maximumFractionDigits: digits }
    ).format(value);
  }

  _currency(entityId) {
    const value = this._numeric(entityId);
    return Number.isFinite(value) ? `${this._format(value)} PLN` : "—";
  }

  _energy(entityId) {
    const state = this._state(entityId);
    let value = Number(state?.state);
    if (!Number.isFinite(value)) return null;
    const unit = String(state?.attributes?.unit_of_measurement || "kWh").toLowerCase();
    if (unit === "wh") value /= 1000;
    else if (unit === "mwh") value *= 1000;
    return value;
  }

  _power(entityId) {
    const state = this._state(entityId);
    let value = Number(state?.state);
    if (!Number.isFinite(value)) return null;
    const unit = String(state?.attributes?.unit_of_measurement || "W").toLowerCase();
    if (unit === "mw") value *= 1000;
    else if (unit !== "kw") value /= 1000;
    return value;
  }

  _periods() {
    const copy = this._copy();
    return [
      { key: "day", label: copy.day, revenue: this._config.daily_revenue_entity, export: this._config.daily_export_entity },
      { key: "week", label: copy.week, revenue: this._config.weekly_revenue_entity, export: this._config.weekly_export_entity },
      { key: "month", label: copy.month, revenue: this._config.monthly_revenue_entity, export: this._config.monthly_export_entity },
      { key: "year", label: copy.year, revenue: this._config.yearly_revenue_entity, export: this._config.yearly_export_entity },
    ];
  }

  _update() {
    if (!this._config || !this.isConnected) return;
    const ids = [
      ...this._periods().flatMap((period) => [period.revenue, period.export]),
      this._config.total_revenue_entity,
      this._config.total_export_entity,
      this._config.current_price_entity,
      this._config.export_power_entity,
      this._config.optimization_gain_entity,
      this._config.optimization_plan_entity,
    ];
    const key = `${hoymilesLanguage(this._hass, this._config.language)}|${ids.map((id) => `${id}:${this._state(id)?.state || "missing"}`).join("|")}`;
    if (key === this._renderKey) return;
    this._renderKey = key;
    this._render();
  }

  _render() {
    const copy = this._copy();
    const currentPrice = this._numeric(this._config.current_price_entity);
    const exportPower = this._power(this._config.export_power_entity);
    const plan = this._state(this._config.optimization_plan_entity);
    const netGain = Number(plan?.attributes?.net_optimization_gain_pln);
    const gain = Number.isFinite(netGain)
      ? netGain
      : this._numeric(this._config.optimization_gain_entity);
    const totalRevenue = this._numeric(this._config.total_revenue_entity);
    const totalExport = this._energy(this._config.total_export_entity);
    const periods = this._periods().map((period) => {
      const revenue = this._numeric(period.revenue);
      const energy = this._energy(period.export);
      return {
        ...period,
        revenue,
        energy,
        average: Number.isFinite(revenue) && Number.isFinite(energy) && energy > 0
          ? revenue / energy
          : null,
      };
    });
    this.shadowRoot.innerHTML = `
      <style>
        ${HOYMILES_AURORA_THEME_CSS}
        :host { container-type: inline-size; display: block; --hoymiles-aurora-accent: ${hoymilesAuroraAccent("grid")}; }
        * { box-sizing: border-box; }
        button { font: inherit; }
        ha-card { background: var(--hoymiles-aurora-surface); border: 1px solid var(--hoymiles-aurora-border); border-radius: var(--ha-card-border-radius); box-shadow: var(--hoymiles-aurora-shadow); color: var(--hoymiles-aurora-text); overflow: hidden; padding: 20px; }
        .eyebrow { color: var(--hoymiles-aurora-grid); font-size: 10px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
        h2 { font-size: 21px; font-weight: 620; line-height: 1.25; margin: 4px 0 0; }
        .headline { display: grid; gap: 8px; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 15px; }
        .headline-item { background: color-mix(in srgb, var(--card-background-color) 89%, var(--hoymiles-aurora-grid) 11%); border: 1px solid color-mix(in srgb, var(--hoymiles-aurora-grid) 18%, var(--divider-color)); border-radius: 12px; min-width: 0; padding: 10px 11px; }
        .label { color: var(--hoymiles-aurora-muted); display: block; font-size: 9px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .headline-value { display: block; font-size: 15px; font-variant-numeric: tabular-nums; font-weight: 650; margin-top: 3px; white-space: nowrap; }
        .periods { display: grid; gap: 8px; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 13px; }
        .period { background: color-mix(in srgb, var(--card-background-color) 92%, var(--primary-text-color) 8%); border: 1px solid var(--divider-color); border-radius: 14px; color: inherit; cursor: pointer; min-width: 0; padding: 12px; text-align: left; transition: border-color .16s ease, transform .16s ease; }
        .period:hover { border-color: color-mix(in srgb, var(--hoymiles-aurora-grid) 42%, transparent); transform: translateY(-1px); }
        .period-title { color: var(--hoymiles-aurora-muted); display: block; font-size: 10px; text-transform: uppercase; }
        .revenue { color: var(--hoymiles-aurora-grid); display: block; font-size: 21px; font-variant-numeric: tabular-nums; font-weight: 700; letter-spacing: -.035em; margin-top: 7px; white-space: nowrap; }
        .detail { color: var(--hoymiles-aurora-muted); display: block; font-size: 10px; line-height: 1.45; margin-top: 6px; }
        .detail strong { color: var(--hoymiles-aurora-text); font-weight: 600; }
        .lifetime { align-items: center; border-top: 1px solid var(--divider-color); display: flex; flex-wrap: wrap; gap: 8px 22px; justify-content: space-between; margin-top: 14px; padding-top: 12px; }
        .lifetime-label { color: var(--hoymiles-aurora-muted); font-size: 10px; }
        .lifetime-values { display: flex; flex-wrap: wrap; gap: 8px 20px; font-size: 14px; font-variant-numeric: tabular-nums; font-weight: 650; }
        @container (max-width: 620px) { ha-card { border-radius: 16px; padding: 15px 12px; } h2 { font-size: 18px; } .headline { grid-template-columns: 1fr; gap: 5px; } .headline-item { align-items: center; display: flex; justify-content: space-between; padding: 8px 10px; } .headline-value { font-size: 13px; margin: 0; } .periods { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; } .period { padding: 10px; } .revenue { font-size: 18px; } }
        @container (max-width: 360px) { .lifetime { display: block; } .lifetime-values { margin-top: 6px; } }
        @media (prefers-reduced-motion: reduce) { .period { transition: none; } }
      </style>
      <ha-card>
        <div class="eyebrow">${hoymilesEscape(copy.eyebrow)}</div>
        <h2>${hoymilesEscape(this._config.title || copy.title)}</h2>
        <div class="headline">
          <div class="headline-item"><span class="label">${hoymilesEscape(copy.currentPrice)}</span><strong class="headline-value">${Number.isFinite(currentPrice) ? `${this._format(currentPrice, 3)} PLN/kWh` : "—"}</strong></div>
          <div class="headline-item"><span class="label">${hoymilesEscape(copy.exportPower)}</span><strong class="headline-value">${Number.isFinite(exportPower) ? `${this._format(Math.abs(exportPower))} kW` : "—"}</strong></div>
          <div class="headline-item"><span class="label">${hoymilesEscape(copy.gain)}</span><strong class="headline-value">${Number.isFinite(gain) ? `${this._format(gain)} PLN` : "—"}</strong></div>
        </div>
        <div class="periods">${periods.map((period) => `<button class="period" type="button" data-entity="${hoymilesEscape(period.revenue)}"><span class="period-title">${hoymilesEscape(period.label)}</span><strong class="revenue">${Number.isFinite(period.revenue) ? `${this._format(period.revenue)} PLN` : "—"}</strong><span class="detail">${hoymilesEscape(copy.export)}: <strong>${Number.isFinite(period.energy) ? `${this._format(period.energy)} kWh` : "—"}</strong><br>${hoymilesEscape(copy.average)}: <strong>${Number.isFinite(period.average) ? `${this._format(period.average, 3)} PLN/kWh` : "—"}</strong></span></button>`).join("")}</div>
        <div class="lifetime"><span class="lifetime-label">${hoymilesEscape(copy.lifetime)}</span><span class="lifetime-values"><span>${Number.isFinite(totalRevenue) ? `${this._format(totalRevenue)} PLN` : "—"}</span><span>${Number.isFinite(totalExport) ? `${this._format(totalExport)} kWh` : "—"}</span></span></div>
      </ha-card>`;
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((button) => button.addEventListener("click", () => hoymilesDispatchMoreInfo(this, button.dataset.entity)));
  }

  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12, rows: 6, min_columns: 6 }; }
}

if (!customElements.get("hoymiles-aurora-finance-card")) {
  customElements.define("hoymiles-aurora-finance-card", HoymilesAuroraFinanceCard);
}
if (!window.customCards.some((card) => card.type === "hoymiles-aurora-finance-card")) {
  window.customCards.push({ type: "hoymiles-aurora-finance-card", name: "Hoymiles Aurora Finance", description: "Premium RCE export and revenue summary.", preview: false });
}

class HoymilesAuroraEnergyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._mounted = false;
    this._hass = null;
    this._config = null;
  }

  setConfig(config) {
    this._config = {
      pv_entity: "sensor.hoymiles_hit_overview_pv_total_power",
      load_entity: "sensor.hoymiles_actual_load_power",
      grid_entity: "sensor.hoymiles_hit_overview_grid_total_active_power",
      battery_entity: "sensor.hoymiles_hit_overview_battery_power",
      battery_soc_entity: "sensor.hoymiles_hit_overview_battery_soc",
      inverter_entity: "sensor.hoymiles_hit_overview_inverter_active_power",
      ems_mode_entity: "select.hoymiles_hit_ems_mode",
      forecast_today_entity: "sensor.hoymiles_solcast_forecast_today",
      forecast_remaining_entity:
        "sensor.hoymiles_solcast_forecast_remaining_today",
      average_load_entity: "sensor.hoymiles_load_average_4_days",
      pv_today_entity: "sensor.hoymiles_hit_pv_total_energy_today",
      pv_to_load_today_entity: "sensor.hoymiles_hit_pv_to_load_energy_today",
      pv_to_battery_today_entity:
        "sensor.hoymiles_hit_pv_to_battery_energy_today",
      pv_to_grid_today_entity: "sensor.hoymiles_hit_pv_to_grid_energy_today",
      grid_import_today_entity: "sensor.hoymiles_hit_grid_energy_buy_today",
      grid_to_load_today_entity: "sensor.hoymiles_rce_grid_to_load_today",
      grid_to_battery_today_entity: "sensor.hoymiles_grid_to_battery_today",
      inverter_image: "/local/hoymiles-inverter.png",
      ...config,
    };
    this._mount();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  connectedCallback() {
    this._mount();
  }

  _mount() {
    if (!this.isConnected || !this._config || this._mounted) return;

    const style = document.createElement("style");
    style.textContent = `
      ${HOYMILES_AURORA_THEME_CSS}
      :host {
        container-type: inline-size;
        display: block;
        --hoymiles-aurora-accent: ${hoymilesAuroraAccent("cyan")};
        --orbit-pv: var(--hoymiles-aurora-pv);
        --orbit-grid: var(--hoymiles-aurora-grid);
        --orbit-load: var(--hoymiles-aurora-load);
        --orbit-battery: var(--hoymiles-aurora-battery);
        --orbit-muted: var(--hoymiles-aurora-offline);
      }
      * { box-sizing: border-box; }
      button { font: inherit; }
      ha-card {
        background:
          radial-gradient(circle at 50% 42%, rgba(31, 123, 255, .16), transparent 31%),
          radial-gradient(circle at 10% 0%, rgba(52, 229, 139, .08), transparent 32%),
          linear-gradient(145deg, #111925 0%, #0a111b 54%, #080d15 100%);
        border: 1px solid rgba(151, 177, 209, .14);
        border-radius: 24px;
        box-shadow: 0 24px 70px rgba(0, 0, 0, .34);
        color: #f6f9ff;
        overflow: hidden;
        position: relative;
      }
      ha-card::before {
        background-image:
          linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px);
        background-size: 30px 30px;
        content: "";
        inset: 0;
        mask-image: linear-gradient(to bottom, black, transparent 82%);
        pointer-events: none;
        position: absolute;
      }
      .shell { padding: 22px 24px 20px; position: relative; z-index: 1; }
      .header {
        align-items: center;
        display: flex;
        justify-content: space-between;
        min-height: 28px;
      }
      .brand {
        color: rgba(223, 235, 249, .63);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .22em;
        text-transform: uppercase;
      }
      .system-state {
        align-items: center;
        background: rgba(255,255,255,.055);
        border: 1px solid rgba(255,255,255,.08);
        border-radius: 999px;
        color: #d9e6f5;
        display: inline-flex;
        font-size: 11px;
        gap: 7px;
        max-width: 58%;
        overflow: hidden;
        padding: 6px 10px;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .system-state .dot {
        background: #34e58b;
        border-radius: 50%;
        box-shadow: 0 0 12px rgba(52,229,139,.9);
        flex: 0 0 auto;
        height: 7px;
        width: 7px;
      }
      .system-state.warn .dot { background: #ffd45a; box-shadow: 0 0 12px rgba(255,212,90,.85); }
      .system-state.offline .dot { background: #ff6577; box-shadow: 0 0 12px rgba(255,101,119,.85); }
      .insights {
        display: grid;
        gap: 10px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        margin-top: 15px;
      }
      .insight, .daily-item, .grid-import-item, .metric {
        appearance: none;
        border: 0;
        color: inherit;
        cursor: pointer;
      }
      .insight {
        background: rgba(255,255,255,.045);
        border: 1px solid rgba(255,255,255,.075);
        border-radius: 13px;
        min-width: 0;
        padding: 10px 12px;
        text-align: left;
        transition: background .18s ease, border-color .18s ease, transform .18s ease;
      }
      .insight:hover, .daily-item:hover, .grid-import-item:hover {
        background: rgba(255,255,255,.075);
        border-color: rgba(255,255,255,.14);
        transform: translateY(-1px);
      }
      .insight-label, .daily-label {
        color: #8496ad;
        display: block;
        font-size: 10px;
        letter-spacing: .025em;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .insight-value {
        display: block;
        font-size: 16px;
        font-variant-numeric: tabular-nums;
        font-weight: 650;
        margin-top: 3px;
      }
      .aurora {
        height: 420px;
        margin: 2px auto 0;
        max-width: 1000px;
        position: relative;
      }
      .flows { height: 100%; inset: 0; overflow: visible; position: absolute; width: 100%; }
      .ribbon {
        fill: none;
        filter: blur(.2px) drop-shadow(0 0 12px var(--flow-color));
        opacity: var(--ribbon-opacity, .22);
        stroke: var(--flow-color);
        stroke-linecap: round;
        stroke-width: var(--ribbon-width, 11);
        transition: opacity .35s ease, stroke-width .35s ease;
      }
      .flow {
        fill: none;
        filter: drop-shadow(0 0 5px var(--flow-color));
        opacity: .94;
        stroke: var(--flow-color);
        stroke-dasharray: 2 13;
        stroke-linecap: round;
        stroke-width: var(--flow-width, 2.6);
        animation: orbit-flow var(--flow-speed, 1.8s) linear infinite;
      }
      .flow.reverse { animation-direction: reverse; }
      .flow.inactive { animation-play-state: paused; opacity: .12; }
      @keyframes orbit-flow { to { stroke-dashoffset: -60; } }
      .metric {
        align-items: center;
        appearance: none;
        backdrop-filter: blur(12px);
        background: rgba(13, 20, 30, .82);
        border: 1px solid rgba(255,255,255,.075);
        border-radius: 13px;
        color: var(--metric-color);
        cursor: pointer;
        display: flex;
        gap: 9px;
        min-width: 128px;
        padding: 9px 11px;
        position: absolute;
        text-align: left;
        transition: background .18s ease, border-color .18s ease, transform .18s ease;
        z-index: 4;
      }
      .metric:hover {
        background: rgba(25, 36, 51, .9);
        border-color: color-mix(in srgb, var(--metric-color) 35%, transparent);
        transform: translateY(-2px);
      }
      .metric-dot {
        background: currentColor;
        border-radius: 50%;
        box-shadow: 0 0 11px currentColor;
        flex: 0 0 auto;
        height: 7px;
        width: 7px;
      }
      .metric-copy { min-width: 0; }
      .metric-name {
        color: #8fa0b5;
        display: block;
        font-size: 9px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .metric-value {
        color: #fff;
        display: block;
        font-size: 14px;
        font-variant-numeric: tabular-nums;
        font-weight: 650;
        letter-spacing: -.02em;
        margin-top: 1px;
        white-space: nowrap;
      }
      .metric.pv { --metric-color: var(--orbit-pv); left: 5%; top: 14%; }
      .metric.grid { --metric-color: var(--orbit-grid); right: 5%; top: 14%; }
      .metric.home { --metric-color: var(--orbit-load); bottom: 8%; left: 5%; }
      .metric.battery { --metric-color: var(--orbit-battery); bottom: 8%; right: 5%; }
      .core {
        align-items: center;
        appearance: none;
        backdrop-filter: blur(9px);
        background: radial-gradient(circle, rgba(29,48,69,.96), rgba(11,18,28,.94) 68%, rgba(11,18,28,.62));
        border: 1px solid rgba(255,255,255,.13);
        border-radius: 50%;
        box-shadow: 0 0 0 16px rgba(98,213,255,.025), 0 0 86px rgba(33,168,255,.18), inset 0 0 32px rgba(255,255,255,.04);
        color: #fff;
        cursor: pointer;
        display: flex;
        height: 148px;
        justify-content: center;
        left: 50%;
        padding: 0;
        position: absolute;
        text-align: center;
        top: 51%;
        transform: translate(-50%, -50%);
        width: 148px;
        z-index: 3;
      }
      .core-value { display: block; font-size: 32px; font-variant-numeric: tabular-nums; font-weight: 680; letter-spacing: -.06em; line-height: 1; }
      .core-name { color: #8fa3bc; display: block; font-size: 10px; margin-top: 3px; }
      .core-status { color: #83f0d0; display: block; font-size: 9px; margin-top: 10px; }
      .daily {
        border-top: 1px solid rgba(255,255,255,.075);
        display: grid;
        gap: 1px;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin-top: -3px;
        padding-top: 15px;
      }
      .daily-item {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 12px;
        min-width: 0;
        padding: 9px 12px;
        text-align: center;
        transition: background .18s ease, border-color .18s ease, transform .18s ease;
      }
      .daily-value {
        display: block;
        font-size: 16px;
        font-variant-numeric: tabular-nums;
        font-weight: 650;
        margin-top: 3px;
      }
      .daily-item:nth-child(1) .daily-value { color: var(--orbit-pv); }
      .daily-item:nth-child(2) .daily-value { color: var(--orbit-load); }
      .daily-item:nth-child(3) .daily-value { color: var(--orbit-battery); }
      .daily-item:nth-child(4) .daily-value { color: var(--orbit-grid); }
      .grid-import {
        align-items: center;
        background: rgba(255, 200, 87, .035);
        border: 1px solid rgba(255, 200, 87, .10);
        border-radius: 14px;
        display: grid;
        gap: 10px;
        grid-template-columns: minmax(135px, .8fr) minmax(0, 2.2fr);
        margin-top: 11px;
        padding: 8px 10px;
      }
      .grid-import-title {
        color: rgba(255, 200, 87, .84);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .075em;
        text-transform: uppercase;
      }
      .grid-import-values {
        display: grid;
        gap: 4px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .grid-import-item {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        min-width: 0;
        padding: 6px 8px;
        text-align: center;
        transition: background .18s ease, border-color .18s ease, transform .18s ease;
      }
      .grid-import-label {
        color: #8496ad;
        display: block;
        font-size: 9px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .grid-import-value {
        color: var(--orbit-grid);
        display: block;
        font-size: 13px;
        font-variant-numeric: tabular-nums;
        font-weight: 650;
        margin-top: 2px;
        white-space: nowrap;
      }
      @media (prefers-reduced-motion: reduce) {
        .flow { animation: none; }
        .insight, .daily-item, .grid-import-item, .metric { transition: none; }
      }
      @container (max-width: 620px) {
        ha-card { border-radius: 19px; }
        .shell { padding: 16px 13px 14px; }
        .header { padding: 0 2px; }
        .brand { font-size: 9px; letter-spacing: .16em; }
        .system-state { font-size: 10px; max-width: 62%; padding: 5px 8px; }
        .insights { gap: 6px; margin-top: 11px; }
        .insight { border-radius: 11px; padding: 8px 8px; text-align: center; }
        .insight-label { font-size: 8px; }
        .insight-value { font-size: 13px; }
        .aurora { height: 390px; margin-top: -2px; }
        .metric { min-width: 106px; padding: 8px 9px; }
        .metric-name { font-size: 8px; }
        .metric-value { font-size: 12px; }
        .metric.pv { left: 2%; top: 15%; }
        .metric.grid { right: 2%; top: 15%; }
        .metric.home { bottom: 8%; left: 2%; }
        .metric.battery { bottom: 8%; right: 2%; }
        .core { height: 132px; top: 51%; width: 132px; }
        .core-value { font-size: 28px; }
        .core-name { font-size: 9px; }
        .core-status { font-size: 8px; }
        .daily { gap: 4px; grid-template-columns: repeat(2, minmax(0, 1fr)); padding-top: 11px; }
        .daily-item { background: rgba(255,255,255,.025); padding: 8px 6px; }
        .daily-label { font-size: 9px; }
        .daily-value { font-size: 14px; }
        .grid-import { grid-template-columns: 1fr; gap: 4px; padding: 8px; }
        .grid-import-title { padding-left: 4px; }
        .grid-import-item { padding: 6px 3px; }
        .grid-import-label { font-size: 8px; }
        .grid-import-value { font-size: 12px; }
      }
    `;

    const card = document.createElement("ha-card");
    card.dataset.auroraVersion = "1.0.0";
    card.innerHTML = `
      <div class="shell">
        <div class="header">
          <span class="brand">Hoymiles · Energy aurora</span>
          <span class="system-state"><span class="dot"></span><span data-value="system"></span></span>
        </div>
        <div class="insights">
          <button class="insight" data-key="forecast_today"><span class="insight-label" data-label="forecast_today"></span><span class="insight-value" data-value="forecast_today"></span></button>
          <button class="insight" data-key="forecast_remaining"><span class="insight-label" data-label="forecast_remaining"></span><span class="insight-value" data-value="forecast_remaining"></span></button>
          <button class="insight" data-key="average_load"><span class="insight-label" data-label="average_load"></span><span class="insight-value" data-value="average_load"></span></button>
        </div>
        <div class="aurora">
          <svg class="flows" viewBox="0 0 900 420" preserveAspectRatio="none" aria-hidden="true">
            <path class="ribbon" data-ribbon="pv" d="M-40 28 C230 28 220 202 450 210"/><path class="flow" data-flow="pv" d="M-40 28 C230 28 220 202 450 210"/>
            <path class="ribbon" data-ribbon="grid" d="M450 210 C660 205 680 32 940 56"/><path class="flow" data-flow="grid" d="M450 210 C660 205 680 32 940 56"/>
            <path class="ribbon" data-ribbon="home" d="M450 210 C250 240 210 425 -40 386"/><path class="flow" data-flow="home" d="M450 210 C250 240 210 425 -40 386"/>
            <path class="ribbon" data-ribbon="battery" d="M450 210 C675 245 685 425 950 390"/><path class="flow" data-flow="battery" d="M450 210 C675 245 685 425 950 390"/>
          </svg>
          <button class="metric pv" data-key="pv"><span class="metric-dot"></span><span class="metric-copy"><span class="metric-name" data-label="pv"></span><strong class="metric-value" data-value="pv"></strong></span></button>
          <button class="metric grid" data-key="grid"><span class="metric-dot"></span><span class="metric-copy"><span class="metric-name" data-label="grid_live"></span><strong class="metric-value" data-value="grid"></strong></span></button>
          <button class="metric home" data-key="load"><span class="metric-dot"></span><span class="metric-copy"><span class="metric-name" data-label="load"></span><strong class="metric-value" data-value="load"></strong></span></button>
          <button class="metric battery" data-key="battery"><span class="metric-dot"></span><span class="metric-copy"><span class="metric-name" data-label="battery_live"></span><strong class="metric-value" data-value="battery"></strong></span></button>
          <button class="core" data-key="pv"><span><strong class="core-value" data-value="core"></strong><span class="core-name" data-label="core"></span><small class="core-status" data-value="core_status"></small></span></button>
        </div>
        <div class="daily">
          <button class="daily-item" data-key="pv_today"><span class="daily-label" data-label="pv_today"></span><span class="daily-value" data-value="pv_today"></span></button>
          <button class="daily-item" data-key="pv_to_load_today"><span class="daily-label" data-label="pv_to_load_today"></span><span class="daily-value" data-value="pv_to_load_today"></span></button>
          <button class="daily-item" data-key="pv_to_battery_today"><span class="daily-label" data-label="pv_to_battery_today"></span><span class="daily-value" data-value="pv_to_battery_today"></span></button>
          <button class="daily-item" data-key="pv_to_grid_today"><span class="daily-label" data-label="pv_to_grid_today"></span><span class="daily-value" data-value="pv_to_grid_today"></span></button>
        </div>
        <div class="grid-import">
          <span class="grid-import-title" data-label="grid_import_title"></span>
          <div class="grid-import-values">
            <button class="grid-import-item" data-key="grid_import_today"><span class="grid-import-label" data-label="grid_import_today"></span><strong class="grid-import-value" data-value="grid_import_today"></strong></button>
            <button class="grid-import-item" data-key="grid_to_load_today"><span class="grid-import-label" data-label="grid_to_load_today"></span><strong class="grid-import-value" data-value="grid_to_load_today"></strong></button>
            <button class="grid-import-item" data-key="grid_to_battery_today"><span class="grid-import-label" data-label="grid_to_battery_today"></span><strong class="grid-import-value" data-value="grid_to_battery_today"></strong></button>
          </div>
        </div>
      </div>`;

    card.querySelectorAll("button[data-key]").forEach((button) => {
      button.addEventListener("click", () => this._showMoreInfo(button.dataset.key));
    });
    this.shadowRoot.replaceChildren(style, card);
    this._card = card;
    this._mounted = true;
    this._update();
  }

  _language() {
    return String(this._config?.language || this._hass?.language || "en")
      .toLowerCase()
      .startsWith("pl")
      ? "pl"
      : "en";
  }

  _copy() {
    const pl = {
      forecast_today: "Prognoza PV dzisiaj",
      forecast_remaining: "Pozostała produkcja",
      average_load: "Średnie zużycie domu",
      pv: "PV",
      grid: "Sieć",
      load: "Dom",
      battery: "Magazyn",
      inverter: "Falownik",
      core: "kW produkcji PV",
      core_status: "● energia zoptymalizowana",
      pv_today: "Wyprodukowano dzisiaj",
      pv_to_load_today: "PV do domu",
      pv_to_battery_today: "PV do magazynu",
      pv_to_grid_today: "PV do sieci",
      grid_import_title: "Pobór z sieci dzisiaj",
      grid_import_today: "Łącznie",
      grid_to_load_today: "Do domu",
      grid_to_battery_today: "Do magazynu",
      production: "produkcja",
      consumption: "zużycie",
      export: "eksport",
      import: "pobór",
      charge: "ładowanie",
      discharge: "rozładowanie",
      idle: "spoczynek",
      noData: "brak danych",
      online: "Dane na żywo",
      partial: "Część danych niedostępna",
      offline: "Brak danych falownika",
      self_use: "Autokonsumpcja",
      grid_charge: "Ładowanie z sieci",
      grid_discharge: "Rozładowanie do sieci",
      off_grid: "Praca wyspowa",
    };
    const en = {
      forecast_today: "PV forecast today",
      forecast_remaining: "Remaining production",
      average_load: "Average home use",
      pv: "PV",
      grid: "Grid",
      load: "Home",
      battery: "Battery",
      inverter: "Inverter",
      core: "kW of PV production",
      core_status: "● optimized energy",
      pv_today: "Produced today",
      pv_to_load_today: "PV to home",
      pv_to_battery_today: "PV to battery",
      pv_to_grid_today: "PV to grid",
      grid_import_title: "Grid import today",
      grid_import_today: "Total",
      grid_to_load_today: "To home",
      grid_to_battery_today: "To battery",
      production: "production",
      consumption: "consumption",
      export: "export",
      import: "import",
      charge: "charging",
      discharge: "discharging",
      idle: "idle",
      noData: "no data",
      online: "Live data",
      partial: "Some data unavailable",
      offline: "Inverter data unavailable",
      self_use: "Self-use",
      grid_charge: "Grid charge",
      grid_discharge: "Grid discharge",
      off_grid: "Off-grid",
    };
    return this._language() === "pl" ? pl : en;
  }

  _entityId(key) {
    const map = {
      pv: "pv_entity",
      load: "load_entity",
      grid: "grid_entity",
      battery: "battery_entity",
      inverter: "inverter_entity",
      forecast_today: "forecast_today_entity",
      forecast_remaining: "forecast_remaining_entity",
      average_load: "average_load_entity",
      pv_today: "pv_today_entity",
      pv_to_load_today: "pv_to_load_today_entity",
      pv_to_battery_today: "pv_to_battery_today_entity",
      pv_to_grid_today: "pv_to_grid_today_entity",
      grid_import_today: "grid_import_today_entity",
      grid_to_load_today: "grid_to_load_today_entity",
      grid_to_battery_today: "grid_to_battery_today_entity",
    };
    return this._config?.[map[key]];
  }

  _showMoreInfo(key) {
    const entityId = this._entityId(key);
    if (!entityId) return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        bubbles: true,
        composed: true,
        detail: { entityId },
      })
    );
  }

  _state(key) {
    const entityId = this._entityId(key);
    return entityId ? this._hass?.states?.[entityId] : undefined;
  }

  _numeric(key) {
    const state = this._state(key);
    const value = Number(state?.state);
    return Number.isFinite(value) ? value : null;
  }

  _powerKw(key) {
    const state = this._state(key);
    const value = Number(state?.state);
    if (!Number.isFinite(value)) return null;
    const unit = String(state?.attributes?.unit_of_measurement || "W").toLowerCase();
    if (unit === "mw") return value * 1000;
    if (unit === "kw") return value;
    return value / 1000;
  }

  _number(value, digits = 2) {
    if (!Number.isFinite(value)) return "—";
    return new Intl.NumberFormat(this._language() === "pl" ? "pl-PL" : "en-GB", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  }

  _formatPower(value, absolute = true) {
    if (!Number.isFinite(value)) return "—";
    return `${this._number(absolute ? Math.abs(value) : value, 2)} kW`;
  }

  _formatEnergy(key) {
    const state = this._state(key);
    let value = Number(state?.state);
    if (!Number.isFinite(value)) return "—";
    const unit = String(state?.attributes?.unit_of_measurement || "kWh").toLowerCase();
    if (unit === "wh") value /= 1000;
    if (unit === "mwh") value *= 1000;
    return `${this._number(Math.abs(value), 1)} kWh`;
  }

  _setText(selector, value) {
    const element = this._card?.querySelector(selector);
    if (element) element.textContent = value;
  }

  _setFlow(name, active, reverse, powerKw) {
    const flow = this._card?.querySelector(`[data-flow="${name}"]`);
    const ribbon = this._card?.querySelector(`[data-ribbon="${name}"]`);
    if (!flow || !ribbon) return;
    flow.classList.toggle("inactive", !active);
    flow.classList.toggle("reverse", Boolean(reverse));
    const magnitude = Math.max(Math.abs(powerKw || 0), 0.02);
    const normalized = Math.min(Math.log10(1 + magnitude) / Math.log10(11), 1);
    flow.style.setProperty("--flow-width", `${1.9 + normalized * 2.1}`);
    flow.style.setProperty("--flow-speed", `${2.5 - normalized * 1.45}s`);
    const colors = {
      pv: "var(--orbit-pv)",
      grid: "var(--orbit-grid)",
      home: "var(--orbit-load)",
      battery: "var(--orbit-battery)",
    };
    flow.style.setProperty("--flow-color", colors[name]);
    ribbon.style.setProperty("--flow-color", colors[name]);
    ribbon.style.setProperty("--ribbon-width", `${7 + normalized * 8}`);
    ribbon.style.setProperty(
      "--ribbon-opacity",
      active ? `${0.11 + normalized * 0.2}` : ".045"
    );
  }

  _update() {
    if (!this._mounted || !this._card || !this._config || !this._hass) return;
    const copy = this._copy();
    Object.keys(copy).forEach((key) => {
      if (["forecast_today", "forecast_remaining", "average_load", "pv", "grid", "load", "battery", "inverter", "core", "pv_today", "pv_to_load_today", "pv_to_battery_today", "pv_to_grid_today", "grid_import_title", "grid_import_today", "grid_to_load_today", "grid_to_battery_today"].includes(key)) {
        this._setText(`[data-label="${key}"]`, copy[key]);
      }
    });

    const pv = this._powerKw("pv");
    const load = this._powerKw("load");
    const grid = this._powerKw("grid");
    const battery = this._powerKw("battery");
    const inverter = this._powerKw("inverter");
    const soc = this._numericStateById(this._config.battery_soc_entity);
    const threshold = 0.02;

    this._setText('[data-value="pv"]', this._formatPower(pv));
    this._setText('[data-value="load"]', this._formatPower(load));
    this._setText('[data-value="grid"]', this._formatPower(grid));
    this._setText('[data-value="battery"]', this._formatPower(battery));
    this._setText('[data-value="core"]', Number.isFinite(pv) ? this._number(Math.abs(pv), 2) : "—");
    this._setText('[data-value="core_status"]', copy.core_status);
    const gridDirection =
      grid === null
        ? copy.noData
        : Math.abs(grid) <= threshold
          ? copy.idle
          : grid > 0
            ? copy.export
            : copy.import;
    this._setText('[data-label="grid_live"]', gridDirection);
    const batteryDirection =
      battery === null
        ? copy.noData
        : Math.abs(battery) <= threshold
          ? copy.idle
          : battery > 0
            ? copy.discharge
            : copy.charge;
    const socText = Number.isFinite(soc) ? ` · ${this._number(soc, 0)}%` : "";
    this._setText('[data-label="battery_live"]', `${batteryDirection}${socText}`);

    this._setFlow("pv", pv !== null && pv > threshold, false, pv);
    this._setFlow("home", load !== null && Math.abs(load) > threshold, false, load);
    this._setFlow("grid", grid !== null && Math.abs(grid) > threshold, grid < 0, grid);
    this._setFlow(
      "battery",
      battery !== null && Math.abs(battery) > threshold,
      battery > 0,
      battery
    );

    this._setText('[data-value="forecast_today"]', this._formatEnergy("forecast_today"));
    this._setText('[data-value="forecast_remaining"]', this._formatEnergy("forecast_remaining"));
    this._setText('[data-value="average_load"]', this._formatEnergy("average_load"));
    for (const key of ["pv_today", "pv_to_load_today", "pv_to_battery_today", "pv_to_grid_today"]) {
      this._setText(`[data-value="${key}"]`, this._formatEnergy(key));
    }
    for (const key of ["grid_import_today", "grid_to_load_today", "grid_to_battery_today"]) {
      this._setText(`[data-value="${key}"]`, this._formatEnergy(key));
    }

    const coreValues = [pv, load, grid, battery];
    const available = coreValues.filter(Number.isFinite).length;
    const system = this._card.querySelector(".system-state");
    system?.classList.toggle("warn", available > 0 && available < coreValues.length);
    system?.classList.toggle("offline", available === 0);
    const emsState = this._hass.states?.[this._config.ems_mode_entity]?.state;
    const emsLabel = copy[emsState] || "";
    this._setText(
      '[data-value="system"]',
      available === 0 ? copy.offline : available < coreValues.length ? copy.partial : emsLabel || copy.online
    );
  }

  _numericStateById(entityId) {
    const value = Number(this._hass?.states?.[entityId]?.state);
    return Number.isFinite(value) ? value : null;
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: 12, rows: 9, min_columns: 6 };
  }
}

if (!customElements.get("hoymiles-aurora-energy-card")) {
  customElements.define("hoymiles-aurora-energy-card", HoymilesAuroraEnergyCard);
}

if (!window.customCards.some((card) => card.type === "hoymiles-aurora-energy-card")) {
  window.customCards.push({
    type: "hoymiles-aurora-energy-card",
    name: "Hoymiles Energy Aurora",
    description: "Premium minimalist live energy-flow card for Hoymiles HIT systems.",
    preview: false,
  });
}

class HoymilesPowerFlowCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._renderVersion = 0;
    this._inverterObserver = null;
    this._batteryEnergySignature = null;
  }

  setConfig(config) {
    if (!config) {
      throw new Error("Power-flow card configuration is required");
    }
    this._config = { ...config };
    this._mount();
  }

  set hass(hass) {
    this._hass = hass;
    const batteryEnergySignature =
      this._resolveBatteryEnergy(hass).signature;
    if (
      this._card &&
      batteryEnergySignature === this._batteryEnergySignature
    ) {
      this._card.hass = hass;
    } else if (this._config) {
      this._mount();
    }
  }

  connectedCallback() {
    this._mount();
  }

  disconnectedCallback() {
    this._inverterObserver?.disconnect();
    this._inverterObserver = null;
  }

  _resolveBatteryEnergy(hass = this._hass) {
    const configuredEnergy = this._config?.battery?.energy;
    if (typeof configuredEnergy !== "string") {
      return {
        value: configuredEnergy,
        signature: `fixed:${configuredEnergy ?? ""}`,
      };
    }

    const state = hass?.states?.[configuredEnergy];
    const numericValue = Number(state?.state);
    const unit = String(
      state?.attributes?.unit_of_measurement ?? ""
    ).trim();
    const unitMultipliers = {
      Wh: 1,
      kWh: 1000,
      MWh: 1000000,
    };
    const multiplier = unitMultipliers[unit] ?? 1;
    const value =
      Number.isFinite(numericValue) && numericValue > 0
        ? numericValue * multiplier
        : 0;

    return {
      value,
      signature: `${configuredEnergy}:${state?.state ?? "unavailable"}:${unit}`,
    };
  }

  _installInverterImage(card, inverterImage) {
    const inverterGroup = card.shadowRoot?.querySelector("svg#Inverter");
    if (!inverterGroup) return false;

    // Hide both variants of the inverter supplied by the underlying card.
    // Leaving them in the SVG keeps the original layout and connection points.
    inverterGroup
      .querySelectorAll(
        'svg[width="54"][height="79"], image[width="54"][height="72"]'
      )
      .forEach((element) => {
        element.style.display = "none";
      });

    let image = inverterGroup.querySelector("#hoymiles-inverter-image");
    if (!image) {
      image = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "image"
      );
      image.id = "hoymiles-inverter-image";
      inverterGroup.append(image);
    }

    // Coordinates are expressed in the original 720 x 405 SVG viewBox.
    // The image therefore follows the diagram scale automatically on phones,
    // tablets and desktop browsers.
    image.setAttribute("x", "205");
    image.setAttribute("y", "177");
    image.setAttribute("width", "70");
    image.setAttribute("height", "84");
    image.setAttribute("preserveAspectRatio", "xMidYMid meet");
    image.setAttribute("href", inverterImage);
    image.setAttribute("aria-hidden", "true");
    image.style.pointerEvents = "none";
    return true;
  }

  async _mount() {
    if (!this.isConnected || !this._config) return;

    const renderVersion = ++this._renderVersion;
    this._inverterObserver?.disconnect();
    this._inverterObserver = null;
    await customElements.whenDefined("sunsynk-power-flow-card");
    if (renderVersion !== this._renderVersion) return;

    const powerFlowConfig = { ...this._config };
    const batteryEnergy = this._resolveBatteryEnergy();
    this._batteryEnergySignature = batteryEnergy.signature;
    if (
      powerFlowConfig.battery &&
      typeof powerFlowConfig.battery.energy === "string"
    ) {
      powerFlowConfig.battery = {
        ...powerFlowConfig.battery,
        energy: batteryEnergy.value,
      };
    }
    const inverterImage =
      powerFlowConfig.inverter_image ??
      "/local/hoymiles-inverter.png";
    delete powerFlowConfig.inverter_image;
    delete powerFlowConfig.inverter_image_left;
    delete powerFlowConfig.inverter_image_top;
    delete powerFlowConfig.inverter_image_width;

    const card = document.createElement("sunsynk-power-flow-card");
    card.setConfig({
      ...powerFlowConfig,
      type: "custom:sunsynk-power-flow-card",
    });
    if (this._hass) {
      card.hass = this._hass;
    }

    const wrapper = document.createElement("div");
    wrapper.className = "wrapper";
    wrapper.append(card);

    const style = document.createElement("style");
    style.textContent = `
      :host {
        display: block;
      }
      .wrapper {
        position: relative;
      }
    `;

    this.shadowRoot.replaceChildren(style, wrapper);
    this._card = card;

    await card.updateComplete;
    if (renderVersion !== this._renderVersion || this._card !== card) return;

    this._installInverterImage(card, inverterImage);
    if (card.shadowRoot) {
      this._inverterObserver = new MutationObserver(() => {
        this._installInverterImage(card, inverterImage);
      });
      this._inverterObserver.observe(card.shadowRoot, {
        childList: true,
        subtree: true,
      });
    }
  }

  getCardSize() {
    return this._card?.getCardSize?.() ?? 6;
  }

  getGridOptions() {
    return this._card?.getGridOptions?.();
  }
}

if (!customElements.get("hoymiles-power-flow-card")) {
  customElements.define("hoymiles-power-flow-card", HoymilesPowerFlowCard);
}

if (!window.customCards.some((card) => card.type === "hoymiles-power-flow-card")) {
  window.customCards.push({
    type: "hoymiles-power-flow-card",
    name: "Hoymiles Power Flow",
    description:
      "Sunsynk power-flow card wrapper with a Hoymiles inverter illustration.",
    preview: false,
  });
}

window.customStrategies = window.customStrategies || [];
if (
  !window.customStrategies.some(
    (strategy) =>
      strategy.type === "hoymiles-hit-xxl-g3" &&
      strategy.strategyType === "dashboard"
  )
) {
  window.customStrategies.push({
    type: "hoymiles-hit-xxl-g3",
    strategyType: "dashboard",
    name: "Hoymiles HIT xxL G3",
    description:
      "Always-current dashboard for the Hoymiles HIT xxL G3 Modbus integration.",
    documentationURL:
      "https://github.com/Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus",
  });
}

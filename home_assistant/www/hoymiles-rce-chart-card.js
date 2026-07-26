class HoymilesRceChartCard extends HTMLElement {
  constructor() {
    super();
    this._renderKey = "";
    this._states = {};
    this._language = document.documentElement.lang || "en";
    this._unsubscribeStates = undefined;
  }

  setConfig(config) {
    if (!config.entity || !config.threshold_entity) {
      throw new Error("entity and threshold_entity are required");
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

  connectedCallback() {
    if (this._unsubscribeStates) return;
    const request = new CustomEvent("context-request", {
      bubbles: true,
      composed: true,
      cancelable: true,
    });
    request.context = "states";
    request.subscribe = true;
    request.callback = (states, unsubscribe) => {
      this._states = states || {};
      this._unsubscribeStates = unsubscribe;
      this._update();
    };
    this.dispatchEvent(request);
  }

  disconnectedCallback() {
    if (typeof this._unsubscribeStates === "function") {
      this._unsubscribeStates();
    }
    this._unsubscribeStates = undefined;
  }

  _update() {
    if (!this._config) return;
    const ids = [
      this._config.entity,
      this._config.threshold_entity,
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
      threshold_entity: "input_number.hoymiles_rce_price_threshold",
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
          current: "Bieżąca",
          threshold: "Próg",
          exportLockout: "Blokada sprzedaży",
          disabled: "wyłączona",
          periods15: "okresów po 15 min",
          blocks30: "bloków sterowania po 30 min",
          average30: "Średnia bloku 30 min",
          lockoutHint: "Blokada sprzedaży",
          planned: "Zaplanowane rozładowanie",
          selfUse: "Autokonsumpcja",
          thresholdShort: "próg",
          thresholdOutside: "Próg jest poza zakresem dzisiejszych cen",
          chartLabel: "Wykres cen RCE dla",
          belowThreshold: "Cena poniżej progu",
          plannedDischarge: "Planowane rozładowanie",
          currentQuarter: "Aktualny kwadrans",
          userThreshold: "Próg użytkownika",
          minimum: "Min.",
          maximum: "Maks.",
          plan: "Plan:",
          active: "● RCE rozładowuje",
          inactive: "○ RCE nieaktywne",
        }
      : {
          defaultTitle: "RCE — today's prices",
          noData: "No complete PSE data",
          noDataHint: "The automation will not discharge without a current plan.",
          current: "Current",
          threshold: "Threshold",
          exportLockout: "Export lockout",
          disabled: "disabled",
          periods15: "15-minute periods",
          blocks30: "30-minute control blocks",
          average30: "30-minute block average",
          lockoutHint: "Export lockout",
          planned: "Planned discharge",
          selfUse: "Self-Use",
          thresholdShort: "threshold",
          thresholdOutside: "The threshold is outside today's price range",
          chartLabel: "RCE price chart for",
          belowThreshold: "Price below threshold",
          plannedDischarge: "Planned discharge",
          currentQuarter: "Current quarter-hour",
          userThreshold: "User threshold",
          minimum: "Min.",
          maximum: "Max.",
          plan: "Plan:",
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
    const thresholdState = this._states[this._config.threshold_entity];
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
      this._setContent(`
        <ha-card>
          <div class="header">${title}</div>
          <div class="empty">
            <ha-icon icon="mdi:cloud-alert"></ha-icon>
            <div>
              <strong>${this._t("noData")}</strong>
              <span>${this._t("noDataHint")}</span>
            </div>
          </div>
        </ha-card>
      `);
      return;
    }

    const threshold = Number(thresholdState?.state);
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
    const plannedBlocks = Number.isFinite(threshold)
      ? halfHours.filter((price, index) => price > threshold && !isBlocked(index * 30))
          .length
      : 0;

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
      const planned =
        !blocked && Number.isFinite(threshold) && halfHours[pair] > threshold;
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
            <div class="badge">
              <span>${this._t("current")}</span>
              <strong>${this._number(currentPrice, 4)} PLN/kWh</strong>
            </div>
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
        ha-card {
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
          background: color-mix(in srgb, #477bd3 15%, var(--card-background-color));
          border: 1px solid color-mix(in srgb, #477bd3 42%, transparent);
          border-radius: 9px;
          min-width: 112px;
          padding: 6px 9px;
        }
        .badge.threshold {
          background: color-mix(in srgb, #ff9800 14%, var(--card-background-color));
          border-color: color-mix(in srgb, #ff9800 42%, transparent);
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
          fill: #477bd3;
        }
        .bar.planned {
          fill: #22a06b;
        }
        .bar.negative {
          fill: #e95f5f;
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
          stroke: #ff9800;
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
        .swatch.normal { background: #477bd3; }
        .swatch.planned { background: #22a06b; }
        .swatch.blocked { background: #777b82; }
        .swatch.current {
          background: transparent;
          border: 2px solid #ffd54f;
          box-sizing: border-box;
        }
        .line {
          border-top: 2px dashed #ff9800;
          display: inline-block;
          width: 18px;
        }
        .active { color: #22a06b; font-weight: 600; }
        .inactive { color: var(--secondary-text-color); }
        .range-note { color: #ff9800; }
        .empty {
          align-items: center;
          color: var(--secondary-text-color);
          display: flex;
          gap: 14px;
          padding: 28px 4px 18px;
        }
        .empty ha-icon {
          color: #ff9800;
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
window.customCards.push({
  type: "hoymiles-rce-chart-card",
  name: "Hoymiles RCE Chart",
  description: "Readable chart of 96 RCE prices with a 48-block EMS plan.",
  preview: true,
});

(function registerHoymilesDashboardStrategy() {
  "use strict";

  const scriptUrl = document.currentScript?.src;
  const assetBase = scriptUrl
    ? new URL(".", scriptUrl)
    : new URL("/local/", window.location.origin);

  class HoymilesHitDashboardBootstrapStrategy extends HTMLElement {
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
        assetBase
      );
      dashboardUrl.search = "";
      const response = await fetch(dashboardUrl, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(
          `Cannot load Hoymiles dashboard (${response.status} ${response.statusText})`
        );
      }
      const dashboard = await response.json();
      if (config?.title) {
        dashboard.title = config.title;
      }
      return dashboard;
    }
  }

  const elementName = "ll-strategy-dashboard-hoymiles-hit-xxl-g3";
  if (!customElements.get(elementName)) {
    customElements.define(
      elementName,
      HoymilesHitDashboardBootstrapStrategy
    );
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
})();

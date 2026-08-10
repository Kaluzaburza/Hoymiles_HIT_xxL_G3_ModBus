"""Structural release validation without requiring a Home Assistant checkout."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import py_compile
import re
import struct
import sys
import tempfile
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components"
COMPONENT = COMPONENT_ROOT / "hoymiles_hit_modbus"
RESOURCES = COMPONENT / "resources"


def require(condition: bool, message: str) -> None:
    """Raise a readable release validation error."""
    if not condition:
        raise RuntimeError(message)


def load_json(path: Path) -> dict | list:
    """Load and validate UTF-8 JSON."""
    with path.open(encoding="utf-8") as json_file:
        return json.load(json_file)


def load_localization_module():
    """Load the standalone localization module without Home Assistant."""
    path = COMPONENT / "localization.py"
    spec = importlib.util.spec_from_file_location("hoymiles_localization", path)
    require(spec is not None and spec.loader is not None, "Cannot load localization")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_assets_module():
    """Load the asset installer with a minimal Home Assistant type stub."""
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")
    core.HomeAssistant = object
    storage.Store = object
    homeassistant.core = core
    homeassistant.helpers = helpers
    helpers.storage = storage
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.core", core)
    sys.modules.setdefault("homeassistant.helpers", helpers)
    sys.modules.setdefault("homeassistant.helpers.storage", storage)

    custom_components = types.ModuleType("custom_components")
    package = types.ModuleType("custom_components.hoymiles_hit_modbus")
    package.__path__ = [str(COMPONENT)]
    sys.modules.setdefault("custom_components", custom_components)
    sys.modules.setdefault("custom_components.hoymiles_hit_modbus", package)

    const_module = types.ModuleType("custom_components.hoymiles_hit_modbus.const")
    const_module.DOMAIN = "hoymiles_hit_modbus"
    const_module.VERSION = json.loads(
        (COMPONENT / "manifest.json").read_text(encoding="utf-8")
    )["version"]
    sys.modules[const_module.__name__] = const_module

    path = COMPONENT / "assets.py"
    spec = importlib.util.spec_from_file_location(
        "custom_components.hoymiles_hit_modbus.assets",
        path,
    )
    require(spec is not None and spec.loader is not None, "Cannot load assets")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_fresh_asset_install() -> None:
    """Exercise fresh installation and the legacy-dashboard migration path."""
    assets = load_assets_module()
    with tempfile.TemporaryDirectory(prefix="hoymiles_hacs_install_") as tmp:
        config_path = Path(tmp)
        dashboard_path = config_path / "dashboard_hoymiles.yaml"
        package_path = (
            config_path / "packages" / "hoymiles_ems_scheduler.yaml"
        )
        polish_written = assets._copy_assets(config_path, "pl-PL", False)
        require(
            len(polish_written) == 4,
            "Fresh Polish installation did not copy all four assets",
        )
        require(
            dashboard_path.read_text(encoding="utf-8")
            == (RESOURCES / "dashboard_hoymiles_pl.yaml").read_text(
                encoding="utf-8"
            ),
            "Fresh Polish installation copied the wrong dashboard",
        )
        require(
            assets._copy_assets(config_path, "pl-PL", False) == [],
            "Asset installer overwrites user files without explicit permission",
        )

        legacy_dashboard = """\
title: Custom user dashboard
entities:
  - sensor.hoymiles_inverter_pv1_voltage
  - sensor.pv_hoymiles_inverter_pv1_current
  - sensor.unrelated_user_entity
"""
        dashboard_path.write_text(legacy_dashboard, encoding="utf-8")
        legacy_package = """\
script:
  custom_user_script:
    sequence:
      - action: select.select_option
        target:
          entity_id: select.pv_hoymiles_inverter_tryb_ems
"""
        package_path.write_text(legacy_package, encoding="utf-8")
        migrated = assets._copy_assets(config_path, "pl-PL", False)
        require(
            migrated == [dashboard_path, package_path],
            "Existing legacy assets were not migrated in place",
        )
        migrated_text = dashboard_path.read_text(encoding="utf-8")
        require(
            "sensor.hoymiles_hit_pv1_voltage" in migrated_text
            and "sensor.hoymiles_hit_pv1_current" in migrated_text,
            "Legacy dashboard ids were not replaced with stable proxy ids",
        )
        require(
            "title: Custom user dashboard" in migrated_text
            and "sensor.unrelated_user_entity" in migrated_text,
            "Legacy migration did not preserve user dashboard content",
        )
        backup_path = dashboard_path.with_name(
            f"{dashboard_path.name}{assets.LEGACY_ENTITY_BACKUP_SUFFIX}"
        )
        require(
            backup_path.read_text(encoding="utf-8") == legacy_dashboard,
            "Legacy dashboard migration did not create an exact backup",
        )
        migrated_package = package_path.read_text(encoding="utf-8")
        require(
            "select.hoymiles_hit_ems_mode" in migrated_package
            and "custom_user_script" in migrated_package,
            "Legacy EMS package was not safely migrated",
        )
        package_backup = package_path.with_name(
            f"{package_path.name}{assets.LEGACY_ENTITY_BACKUP_SUFFIX}"
        )
        require(
            package_backup.read_text(encoding="utf-8") == legacy_package,
            "Legacy EMS migration did not create an exact backup",
        )
        require(
            assets._copy_assets(config_path, "pl-PL", False) == [],
            "Stable dashboard migration is not idempotent",
        )

        english_written = assets._copy_assets(config_path, "en-GB", True)
        require(
            len(english_written) == 4,
            "English overwrite installation did not copy all four assets",
        )
        require(
            (config_path / "dashboard_hoymiles.yaml").read_text(
                encoding="utf-8"
            )
            == (RESOURCES / "dashboard_hoymiles_en.yaml").read_text(
                encoding="utf-8"
            ),
            "English installation copied the wrong dashboard",
        )

    with tempfile.TemporaryDirectory(prefix="hoymiles_managed_upgrade_") as tmp:
        config_path = Path(tmp)
        dashboard_path = config_path / "dashboard_hoymiles.yaml"
        dashboard_path.write_text("title: previous managed release\n", encoding="utf-8")
        previous_hash = assets._sha256(dashboard_path)
        written, managed = assets._sync_assets(
            config_path,
            "pl-PL",
            False,
            {"dashboard_hoymiles.yaml": previous_hash},
        )
        require(
            dashboard_path in written
            and managed["dashboard_hoymiles.yaml"]
            == assets._sha256(dashboard_path),
            "An unchanged managed dashboard was not upgraded",
        )

        dashboard_path.write_text("title: user customization\n", encoding="utf-8")
        custom_content = dashboard_path.read_text(encoding="utf-8")
        written, managed = assets._sync_assets(
            config_path,
            "pl-PL",
            False,
            {"dashboard_hoymiles.yaml": previous_hash},
        )
        require(
            dashboard_path not in written
            and dashboard_path.read_text(encoding="utf-8") == custom_content
            and "dashboard_hoymiles.yaml" not in managed,
            "A user-modified dashboard was overwritten",
        )

    with tempfile.TemporaryDirectory(prefix="hoymiles_storage_upgrade_") as tmp:
        config_path = Path(tmp)
        storage_path = config_path / ".storage"
        storage_path.mkdir()
        resources_path = storage_path / "lovelace_resources"
        resources_payload = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace_resources",
            "data": {
                "items": [
                    {
                        "id": "legacy-hoymiles-resource",
                        "url": "/local/hoymiles-rce-chart-card.js?v=old",
                        "type": "module",
                    },
                    {
                        "id": "unrelated-resource",
                        "url": "/local/user-card.js",
                        "type": "module",
                    },
                ]
            },
        }
        resources_path.write_text(
            json.dumps(resources_payload), encoding="utf-8"
        )

        dashboard_path = storage_path / "lovelace.hoymiles_test"
        dashboard_payload = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace.hoymiles_test",
            "data": {
                "config": {
                    "title": "Custom user layout",
                    "views": [
                        {
                            "cards": [
                                {
                                    "type": "entities",
                                    "title": "User card",
                                    "entities": [
                                        "sensor.hoymiles_hit_overview_pv_total_power",
                                        "sensor.hoymiles_hit_overview_battery_power",
                                        "sensor.unrelated_user_entity",
                                        {
                                            "entity": "sensor.hoymiles_rce_pv_self_consumption_today",
                                            "name": "PV → odbiorniki dzisiaj",
                                        },
                                        {
                                            "entity": "sensor.hoymiles_rce_battery_to_load_today",
                                            "name": "Bateria → odbiorniki dzisiaj",
                                        },
                                        {
                                            "entity": "sensor.hoymiles_rce_grid_to_load_today",
                                            "name": "Sieć → odbiorniki dzisiaj",
                                        },
                                    ],
                                },
                                {
                                    "type": "markdown",
                                    "content": "User content",
                                },
                                {
                                    "type": "custom:hoymiles-power-flow-card",
                                    "inverter_image": (
                                        "/api/hoymiles_hit_modbus/static/"
                                        "hoymiles-inverter.png"
                                    ),
                                },
                            ]
                        }
                    ],
                }
            },
        }
        dashboard_path.write_text(
            json.dumps(dashboard_payload), encoding="utf-8"
        )

        unrelated_path = storage_path / "lovelace.unrelated"
        unrelated_payload = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace.unrelated",
            "data": {
                "config": {
                    "views": [
                        {"cards": [{"type": "entities", "entities": []}]}
                    ]
                }
            },
        }
        unrelated_text = json.dumps(unrelated_payload)
        unrelated_path.write_text(unrelated_text, encoding="utf-8")

        migrated = assets._sync_lovelace_storage(config_path)
        require(
            migrated == [resources_path, dashboard_path],
            "Storage-mode dashboard migration changed unexpected files",
        )
        migrated_dashboard = json.loads(
            dashboard_path.read_text(encoding="utf-8")
        )
        cards = migrated_dashboard["data"]["config"]["views"][0]["cards"]
        require(
            cards[0]["type"] == assets.ZEBRA_CARD_TYPE,
            "Storage-mode entities cards were not upgraded to zebra cards",
        )
        require(
            cards[0]["title"] == "User card"
            and "sensor.unrelated_user_entity" in cards[0]["entities"]
            and cards[1]["content"] == "User content"
            and cards[2]["inverter_image"] == assets.INVERTER_IMAGE_PATH,
            "Storage-mode migration did not preserve user customizations",
        )
        migrated_rows = [
            row
            for row in cards[0]["entities"]
            if isinstance(row, dict)
        ]
        require(
            migrated_rows[0]
            == {
                "entity": "sensor.hoymiles_actual_load_energy_today",
                "name": "Rzeczywiste zużycie odbiorników dzisiaj",
            }
            and migrated_rows[1]["name"]
            == "PV → odbiorniki — rejestr diagnostyczny"
            and migrated_rows[2]["name"]
            == "Energia oddana przez baterię — diagnostycznie"
            and migrated_rows[3]["name"]
            == "Energia pobrana z sieci — diagnostycznie",
            "Storage-mode RCE LOAD rows were not migrated safely",
        )
        migrated_resources = json.loads(
            resources_path.read_text(encoding="utf-8")
        )["data"]["items"]
        require(
            migrated_resources[0]["url"] == assets.FRONTEND_RESOURCE_URL
            and migrated_resources[0]["type"] == "module"
            and migrated_resources[1]["url"] == "/local/user-card.js",
            "Managed Lovelace resource was not cache-busted safely",
        )
        require(
            dashboard_path.with_name(
                f"{dashboard_path.name}.pre-{assets.VERSION}.bak"
            ).is_file()
            and resources_path.with_name(
                f"{resources_path.name}.pre-{assets.VERSION}.bak"
            ).is_file(),
            "Storage migration did not create rollback backups",
        )
        require(
            unrelated_path.read_text(encoding="utf-8") == unrelated_text,
            "Storage migration touched an unrelated dashboard",
        )
        require(
            assets._sync_lovelace_storage(config_path) == [],
            "Storage-mode migration is not idempotent",
        )


def png_dimensions(path: Path) -> tuple[int, int]:
    """Return PNG dimensions using only the Python standard library."""
    header = path.read_bytes()[:24]
    require(
        len(header) == 24
        and header[:8] == b"\x89PNG\r\n\x1a\n"
        and header[12:16] == b"IHDR",
        f"{path.name} is not a valid PNG",
    )
    return struct.unpack(">II", header[16:24])


def entity_translation_keys(translations: dict) -> dict[str, set[str]]:
    """Return translation keys grouped by entity domain."""
    return {
        domain: set(entries)
        for domain, entries in translations.get("entity", {}).items()
    }


def main() -> int:
    """Validate HACS layout, translations, Python and bundled assets."""
    integration_dirs = [
        path for path in COMPONENT_ROOT.iterdir() if path.is_dir()
    ]
    require(
        integration_dirs == [COMPONENT],
        "HACS repositories may contain only one custom integration",
    )

    hacs = load_json(ROOT / "hacs.json")
    require(hacs.get("name"), "hacs.json requires a display name")

    manifest = load_json(COMPONENT / "manifest.json")
    required_manifest = {
        "domain",
        "documentation",
        "issue_tracker",
        "codeowners",
        "name",
        "version",
    }
    require(
        required_manifest <= set(manifest),
        f"manifest.json is missing: {sorted(required_manifest - set(manifest))}",
    )
    require(manifest["domain"] == "hoymiles_hit_modbus", "Unexpected domain")
    require(
        re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]) is not None,
        "Release version must use semantic MAJOR.MINOR.PATCH format",
    )

    entity_source = (COMPONENT / "entity.py").read_text(encoding="utf-8")
    init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assets_source = (COMPONENT / "assets.py").read_text(encoding="utf-8")
    config_flow_source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    sensor_platform_source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    const_source = (COMPONENT / "const.py").read_text(encoding="utf-8")
    require(
        f'VERSION = "{manifest["version"]}"' in const_source,
        "const.py VERSION does not match manifest.json",
    )
    ems_package_version_match = re.search(
        r'^EMS_PACKAGE_VERSION = "([^"]+)"$', const_source, re.MULTILINE
    )
    require(
        ems_package_version_match is not None,
        "const.py is missing EMS_PACKAGE_VERSION",
    )
    expected_ems_package_version = ems_package_version_match.group(1)
    ems_package_source = (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml"
    ).read_text(encoding="utf-8")
    require(
        'EMS_PACKAGE_SENTINEL = "input_boolean.hoymiles_rce_discharge_enabled"'
        in const_source
        and 'EMS_PACKAGE_VERSION_ENTITY = "sensor.hoymiles_ems_package_version"'
        in const_source
        and "EMS_PACKAGE_VERSION_ENTITY," in sensor_platform_source
        and "EMS_PACKAGE_SENTINEL," in init_source
        and "hoymiles_rce_discharge_enabled:" in ems_package_source
        and "hoymiles_rce_automation_enabled" not in init_source
        and "hoymiles_rce_automation_enabled" not in sensor_platform_source,
        "Setup status and Repairs must use an existing shared EMS package sentinel",
    )
    require(
        "ems_package_restart_required" in init_source
        and "_ems_package_restart_issue_id" in init_source
        and "package_version.state == EMS_PACKAGE_VERSION" in init_source
        and "EMS_PACKAGE_VERSION," in sensor_platform_source
        and '"expected_ems_package_version": EMS_PACKAGE_VERSION'
        in sensor_platform_source
        and '"restart_required": self._ems_restart_required'
        in sensor_platform_source,
        "Managed EMS package updates do not expose the required restart state",
    )
    require(
        "def suggested_object_id(self)" in entity_source,
        "Proxy entities need an explicit stable suggested_object_id property",
    )
    require(
        "_async_reconcile_entity_registry(" in init_source
        and "entity_registry.async_remove" in init_source,
        "Localized entity ids and stale catalog proxies are not reconciled",
    )
    require(
        init_source.index("async_forward_entry_setups")
        < init_source.index("_async_reconcile_entity_registry", init_source.index("async_forward_entry_setups")),
        "Entity registry must be reconciled after platform setup",
    )
    require(
        "firmware_update_required" in entity_source
        and "matched.source is not None" in entity_source,
        "Missing-firmware proxy entities are not represented safely",
    )
    require(
        "StaticPathConfig" in init_source
        and 'STATIC_URL = f"/api/{DOMAIN}/{FRONTEND_STATIC_ROUTE}"'
        in init_source
        and 'FRONTEND_STATIC_ROUTE = "static-r2"' in assets_source,
        "Integration assets are not exposed through the stable no-cache URL",
    )
    require(
        "add_extra_js_url" in init_source
        and "FRONTEND_MODULE_URL" in init_source
        and "FRONTEND_RESOURCE_URL" in init_source
        and "?v={VERSION}" in assets_source,
        "Dashboard strategy module is not registered with versioned cache busting",
    )
    require(
        "frontend" in manifest.get("dependencies", []),
        "Frontend dependency is required for automatic strategy registration",
    )
    require(
        "_async_default_source_device_id" in config_flow_source
        and "CONF_COPY_ASSETS: True" in config_flow_source
        and "BooleanSelector" not in config_flow_source,
        "Config flow still exposes avoidable asset-copy choices",
    )
    require(
        "ems_package_not_loaded" in init_source
        and "issue_registry" in init_source
        and "HoymilesSetupStatusSensor" in sensor_platform_source
        and "firmware_coverage_percent" in sensor_platform_source,
        "Beginner setup status or EMS package Repair is missing",
    )

    catalog = load_json(COMPONENT / "entity_catalog.json")
    require(isinstance(catalog, list), "Entity catalog must be a list")
    require(len(catalog) >= 250, "The generated catalog is unexpectedly small")
    identities = {
        (entry["domain"], entry["translation_key"]) for entry in catalog
    }
    require(
        len(identities) == len(catalog),
        "Duplicate domain/translation_key in entity catalog",
    )
    require(
        ("button", "clear_fault") in identities,
        "Generated catalog is missing the Clear Fault button",
    )
    system_package = (ROOT / "packages" / "system.yaml").read_text(
        encoding="utf-8"
    )
    require(
        "create_write_single_command" in system_package
        and "controller, 3004, 1" in system_package,
        "Clear Fault must write value 1 to holding register 3004",
    )
    meter_package = (ROOT / "packages" / "meters.yaml").read_text(
        encoding="utf-8"
    )
    for power_name in (
        "Meter Grid Active Power L1",
        "Meter Grid Active Power L2",
        "Meter Grid Active Power L3",
        "Meter Grid Total Active Power",
    ):
        require(
            re.search(
                rf"modbus_controller_id: \$\{{modbus_fast_controller_id\}}\s+"
                rf'name: "{re.escape(power_name)}"',
                meter_package,
            )
            is not None,
            f"{power_name} must use the fast Modbus polling controller",
        )

    english = load_json(COMPONENT / "translations" / "en.json")
    polish = load_json(COMPONENT / "translations" / "pl.json")
    require(
        "ems_package_restart_required" in english.get("issues", {})
        and "ems_package_restart_required" in polish.get("issues", {}),
        "EMS package restart Repair is not translated in both languages",
    )
    require(
        entity_translation_keys(english) == entity_translation_keys(polish),
        "English and Polish entity translation keys differ",
    )
    for entry in catalog:
        domain = entry["domain"]
        key = entry["translation_key"]
        require(key in english["entity"][domain], f"Missing English key {domain}.{key}")
        require(key in polish["entity"][domain], f"Missing Polish key {domain}.{key}")

    required_assets = [
        RESOURCES / "dashboard_hoymiles_en.yaml",
        RESOURCES / "dashboard_hoymiles_pl.yaml",
        RESOURCES / "home_assistant" / "en" / "hoymiles_ems_scheduler.yaml",
        RESOURCES / "home_assistant" / "pl" / "hoymiles_ems_scheduler.yaml",
        RESOURCES / "www" / "hoymiles-rce-chart-card.js",
        RESOURCES / "www" / "hoymiles-dashboard-strategy.js",
        RESOURCES / "www" / "hoymiles-inverter.png",
        RESOURCES / "www" / "dashboard_hoymiles_en.json",
        RESOURCES / "www" / "dashboard_hoymiles_pl.json",
    ]
    for asset in required_assets:
        require(asset.is_file(), f"Missing bundled asset: {asset.relative_to(ROOT)}")

    for package_path in (
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml",
        required_assets[2],
        required_assets[3],
    ):
        package_text = package_path.read_text(encoding="utf-8")
        require(
            "unique_id: hoymiles_ems_package_version" in package_text
            and f'state: "{expected_ems_package_version}"' in package_text,
            "EMS package version marker does not match "
            f"{expected_ems_package_version} "
            f"in {package_path.relative_to(ROOT)}",
        )

    dashboard_source = (
        ROOT / "dashboard_hoymiles.yaml"
    ).read_text(encoding="utf-8")
    expected_zebra_cards = dashboard_source.count(
        "type: custom:hoymiles-zebra-entities-card"
    )
    require(
        expected_zebra_cards >= 56,
        "Source dashboard unexpectedly lost zebra entity cards",
    )
    rce_plan_index = dashboard_source.index("title: Plan rozładowań RCE")
    rce_details_index = dashboard_source.index(
        "title: RCE — szczegóły i diagnostyka"
    )
    rce_details_end = dashboard_source.index(
        "\n      - type: markdown\n",
        rce_details_index,
    )
    require(
        rce_plan_index < rce_details_index
        and "position: sidebar"
        not in dashboard_source[rce_details_index:rce_details_end],
        "RCE details must remain in the main column below the discharge plan",
    )
    for dashboard_json in required_assets[-2:]:
        dashboard_data = load_json(dashboard_json)
        require(
            isinstance(dashboard_data, dict)
            and isinstance(dashboard_data.get("views"), list)
            and len(dashboard_data["views"]) >= 10,
            f"Invalid dashboard strategy payload: {dashboard_json.name}",
        )
        dashboard_json_text = json.dumps(dashboard_data)
        require(
            dashboard_json_text.count(
                '"type": "custom:hoymiles-zebra-entities-card"'
            )
            == expected_zebra_cards
            and '"type": "entities"' not in dashboard_json_text,
            f"{dashboard_json.name} does not use all "
            f"{expected_zebra_cards} zebra entity cards",
        )

    card_source = required_assets[4].read_text(encoding="utf-8")
    require(
        "ll-strategy-dashboard-hoymiles-hit-xxl-g3" in card_source
        and "dashboard_hoymiles_${language}.json" in card_source
        and "import.meta.url" in card_source
        and "/api/hoymiles_hit_modbus/static/dashboard_hoymiles_" not in card_source
        and "window.customStrategies" in card_source,
        "Dashboard strategy is not update-safe or hard-codes a stale asset route",
    )
    bootstrap_source = required_assets[5].read_text(encoding="utf-8")
    require(
        "ll-strategy-dashboard-hoymiles-hit-xxl-g3" in bootstrap_source
        and "document.currentScript" in bootstrap_source
        and "import.meta" not in bootstrap_source,
        "Classic dashboard bootstrap is missing or uses ES-module-only syntax",
    )
    require(
        "futureNoData" in card_source
        and "will recalculate automatically after publication" in card_source,
        "RCE chart does not explain automatic replanning after tomorrow's data",
    )
    require(
        "unitMultipliers" in card_source
        and "kWh: 1000" in card_source
        and "_resolveBatteryEnergy" in card_source,
        "Power-flow card does not convert the capacity entity from kWh to Wh",
    )
    require(
        "class HoymilesZebraEntitiesCard" in card_source
        and 'customElements.define(\n    "hoymiles-zebra-entities-card"' in card_source
        and "color-mix(" in card_source
        and "var(--primary-text-color) 7%" in card_source,
        "Theme-aware zebra entities card is not registered",
    )
    for dashboard_path in required_assets[:2]:
        dashboard_text = dashboard_path.read_text(encoding="utf-8")
        require(
            "entity: sensor.hoymiles_rce_day_tomorrow\n"
            "            future_data: true" in dashboard_text,
            f"{dashboard_path.name} does not mark the tomorrow chart as future data",
        )
        require(
            "energy: sensor.hoymiles_hit_battery_capacity" in dashboard_text,
            f"{dashboard_path.name} does not use the inverter-configured battery capacity",
        )
        require(
            "invert_power:" not in dashboard_text,
            (
                f"{dashboard_path.name} incorrectly inverts the normalized "
                "Hoymiles battery power sign"
            ),
        )
        require(
            dashboard_text.count(
                "type: custom:hoymiles-zebra-entities-card"
            )
            == expected_zebra_cards
            and not re.search(r"^\s*-?\s*type:\s+entities\s*$", dashboard_text, re.M),
            f"{dashboard_path.name} does not use all "
            f"{expected_zebra_cards} zebra entity cards",
        )
        require(
            dashboard_text.count("type: statistics-graph") >= 12
            and "period: 5minute" in dashboard_text
            and "min_y_axis: 220" in dashboard_text
            and 'color: "#FF1744"' in dashboard_text
            and 'color: "#00B0FF"' in dashboard_text
            and 'color: "#FFD600"' in dashboard_text,
            f"{dashboard_path.name} lacks the native statistics graph set",
        )
        load_graph_title = (
            "Odbiorniki — moc ostatnie 24 godziny [W]"
            if dashboard_path.name.endswith("_pl.yaml")
            else "Loads — power over the last 24 hours [W]"
        )
        load_energy_title = (
            "Zużycie domu — ostatnie 30 dni [kWh]"
            if dashboard_path.name.endswith("_pl.yaml")
            else "Home consumption — last 30 days [kWh]"
        )
        require(
            load_graph_title in dashboard_text
            and load_energy_title in dashboard_text
            and "entity: sensor.hoymiles_actual_load_energy_total"
            in dashboard_text,
            f"{dashboard_path.name} lacks the LOAD power/energy graphs",
        )

    rce_sensor_source = (
        COMPONENT / "rce_sensor.py"
    ).read_text(encoding="utf-8")
    require(
        '"planning_scope": (' in rce_sensor_source
        and '"tomorrow_data_pending": not tomorrow_rows_complete' in rce_sensor_source
        and '"automatic_replan": True' in rce_sensor_source
        and "[*today_rows, *usable_tomorrow_rows]" in rce_sensor_source,
        "RCE sensor lacks the safe today-only planning fallback",
    )
    rce_optimizer_source = (
        COMPONENT / "rce_optimizer.py"
    ).read_text(encoding="utf-8")
    require(
        (
            "exports[candidate.start] = low" in rce_optimizer_source
            or "trial[candidate.start] = low" in rce_optimizer_source
        )
        and "exports[candidate.start] = round(low, 2)" not in rce_optimizer_source,
        "RCE optimizer can still invalidate feasible plans by rounding upward",
    )
    require(
        "minimum_price_pln_kwh" not in rce_optimizer_source
        and "hoymiles_rce_price_threshold" not in rce_sensor_source,
        "RCE optimizer still depends on a manually configured price threshold",
    )
    tariff_optimizer_source = (
        COMPONENT / "tariff_optimizer.py"
    ).read_text(encoding="utf-8")
    tariff_sensor_source = (
        COMPONENT / "tariff_sensor.py"
    ).read_text(encoding="utf-8")
    require(
        "first_shortage_index" in tariff_optimizer_source
        and "accepted_support_kwh" in tariff_optimizer_source
        and "for index in range(len(starts))" in tariff_optimizer_source
        and "trial_simulation.shortage_kwh" in tariff_optimizer_source
        and "minimum_saving_pln_kwh" in tariff_optimizer_source,
        "Tariff optimizer lacks deficit-reducing direct support or charging",
    )
    require(
        "bms_charge_power_limit_kw" in tariff_sensor_source
        and "effective_charge_power_percent" in tariff_sensor_source
        and "forecast_tomorrow_kwh" in tariff_sensor_source,
        "Tariff sensor lacks forecast or BMS-safe charge-power diagnostics",
    )

    stable_entity_assets = [
        ROOT / "dashboard_hoymiles.yaml",
        ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml",
        required_assets[0],
        required_assets[1],
        required_assets[2],
        required_assets[3],
    ]
    stable_entity_pattern = re.compile(
        r"\b(button|sensor|number|select)\.hoymiles_hit_([a-z0-9_]+)\b"
    )
    legacy_entity_pattern = re.compile(
        r"\b(?:button|sensor|number|select)\."
        r"[a-z0-9_]*hoymiles_inverter[a-z0-9_]*\b"
    )
    native_integration_entities = {
        ("sensor", "rce_optimized_plan"),
        ("sensor", "tariff_charge_plan"),
        ("sensor", "rcm_voltage_plan"),
        ("sensor", "setup_status"),
    }
    for asset_path in stable_entity_assets:
        asset_text = asset_path.read_text(encoding="utf-8")
        require(
            not legacy_entity_pattern.search(asset_text),
            "Installation-specific entity id remains in "
            f"{asset_path.relative_to(ROOT)}",
        )
        for domain, translation_key in stable_entity_pattern.findall(
            asset_text
        ):
            require(
                (domain, translation_key) in identities
                or (domain, translation_key) in native_integration_entities,
                f"Asset references an entity absent from the catalog: "
                f"{domain}.hoymiles_hit_{translation_key}",
            )

    for package_path in required_assets[2:4]:
        package_text = package_path.read_text(encoding="utf-8")
        dynamic_reserve_markers = (
            "hoymiles_ems_push_notifications_enabled:",
            "hoymiles_ems_push_notify_target:",
            "unique_id: hoymiles_ems_push_notification_status",
            "id: hoymiles_ems_push_status_notification",
            "action: notify.send_message",
            "input_boolean.hoymiles_rce_dynamic_soc_enabled",
            "input_number.hoymiles_rce_soc_safety_margin",
            "input_text.hoymiles_solcast_forecast_today_entity",
            "input_text.hoymiles_solcast_forecast_tomorrow_entity",
            "hoymiles_rce_inverter_rated_power:",
            "hoymiles_rce_fallback_daily_load:",
            "hoymiles_rce_export_efficiency:",
            "sensor.solcast_pv_forecast_forecast_today",
            "sensor.solcast_pv_forecast_forecast_tomorrow",
            "sensor.solcast_pv_forecast_prognoza_na_dzisiaj",
            "sensor.solcast_pv_forecast_prognoza_na_jutro",
            "unique_id: hoymiles_rce_day_tomorrow",
            "state_characteristic: sum_differences_nonnegative",
            "max_age:\n      days: 4",
            "hoymiles_tariff_charge_enabled:",
            "hoymiles_tariff_type:",
            "hoymiles_tariff_latched_target_soc:",
            "hoymiles_tariff_latched_slot_end:",
            "input_number.hoymiles_tariff_latched_target_soc",
            "input_datetime.hoymiles_tariff_latched_slot_end",
            "'current_slot_end'",
            'for: "00:00:15"',
            "unique_id: hoymiles_tariff_planned_charge_slot",
            "id: hoymiles_automatic_ems_mode_interlock",
            "id: hoymiles_tariff_grid_charge_control",
            "hoymiles_rcm_pre_discharge_enabled:",
            "hoymiles_rcm_pre_discharge_active:",
            "unique_id: hoymiles_rcm_natural_headroom_before_risk",
            "unique_id: hoymiles_rcm_planned_grid_discharge",
            "unique_id: hoymiles_rcm_pre_discharge_power",
            "unique_id: hoymiles_rcm_pre_discharge_target_soc",
            "id: hoymiles_rcm_pre_discharge_control",
            "input_boolean.hoymiles_rcm_shadow_mode",
            "binary_sensor.hoymiles_sale_block_active",
            "number.hoymiles_hit_maximum_discharge_power",
            "number.hoymiles_hit_force_discharge_soc",
            "hoymiles_battery_balancing_enabled:",
            "hoymiles_battery_balancing_active:",
            "hoymiles_battery_balancing_interval_days:",
            "hoymiles_battery_balancing_hold_hours:",
            "unique_id: hoymiles_battery_balancing_slow_charge_power",
            "unique_id: hoymiles_battery_balancing_due",
            "hoymiles_start_battery_balancing:",
            "hoymiles_stop_battery_balancing:",
            "id: hoymiles_battery_balancing_control",
            "timer.hoymiles_battery_balancing_watchdog",
            "input_boolean.hoymiles_battery_balancing_active",
            "sensor.hoymiles_actual_load_energy_today",
            "unique_id: hoymiles_actual_load_energy_total",
            "unique_id: hoymiles_actual_load_energy_daily",
            "source: sensor.hoymiles_actual_load_energy_total",
            "source_entity: sensor.hoymiles_actual_load_power",
            "sensor.hoymiles_actual_load_power",
            "sensor.hoymiles_hit_load_power_l1n",
            "sensor.hoymiles_hit_load_power_l2n",
            "sensor.hoymiles_hit_load_power_l3n",
            "sensor.hoymiles_night_protected_load_power",
            "sensor.hoymiles_night_protection_window_remaining",
            "sensor.hoymiles_protected_window_expected_load",
            "{% set upcoming_start = setting - buffer %}",
            "{% set upcoming_end = rising + buffer %}",
            "upcoming_end - upcoming_start",
            "sensor.hoymiles_rce_protected_home_energy",
            "state_attr('sun.sun', 'next_rising')",
            "state_attr('sun.sun', 'next_setting')",
            "sensor.hoymiles_hit_battery_capacity",
            "number.hoymiles_hit_self_use_soc",
            "sensor.hoymiles_hit_rce_optimized_plan",
            "sensor.hoymiles_rce_dynamic_minimum_soc",
            "sensor.hoymiles_rce_effective_minimum_soc",
            "hoymiles_rce_accounting_date:",
            "hoymiles_rce_grid_sell_checkpoint:",
            "hoymiles_rce_realized_controlled_export_store:",
            "hoymiles_rce_realized_natural_export_store:",
            "hoymiles_rce_realized_controlled_revenue_store:",
            "hoymiles_rce_realized_natural_revenue_store:",
            "hoymiles_rce_unclassified_export_store:",
            "id: hoymiles_rce_export_accounting",
            "unique_id: hoymiles_rce_self_use_baseline_export",
            "unique_id: hoymiles_rce_realized_controlled_export_today",
            "unique_id: hoymiles_rce_realized_natural_export_today",
            "unique_id: hoymiles_rce_realized_controlled_revenue_today",
            "unique_id: hoymiles_rce_realized_natural_revenue_today",
            "unique_id: hoymiles_rce_realized_revenue_today",
            "unique_id: hoymiles_rce_unclassified_export_today",
            "([grid / 1000, 0] | max)",
            "binary_sensor.hoymiles_rce_reserve_ready",
            "or is_state('binary_sensor.hoymiles_rce_reserve_ready', 'off')",
        )
        for marker in dynamic_reserve_markers:
            require(
                marker in package_text,
                f"Dynamic RCE reserve marker missing in {package_path.name}: {marker}",
            )
        require(
            'source_registers: "2129 + 2130 + 2131"' not in package_text
            and "sensor.hoymiles_hit_load_energy_use_l1n_today"
            not in package_text
            and "sensor.hoymiles_hit_load_energy_use_l2n_today"
            not in package_text
            and "sensor.hoymiles_hit_load_energy_use_l3n_today"
            not in package_text,
            f"Clean home-energy calculation regressed to inverter daily LOAD "
            f"counters in {package_path.name}",
        )
        require(
            "or not is_state(\n"
            "                       'binary_sensor.hoymiles_tariff_planned_charge_slot', 'on')"
            not in package_text,
            f"Tariff control still stops on a transient live-plan change in "
            f"{package_path.name}",
        )
        require(
            "[-grid / 1000, 0]" not in package_text,
            f"Grid export power still uses the reversed sign in {package_path.name}",
        )
        require(
            not re.search(
                r"device_class:\s*energy\s+state_class:\s*measurement",
                package_text,
            ),
            f"Energy helper uses invalid measurement state class in {package_path.name}",
        )

    rcm_optimizer_source = (
        COMPONENT / "rcm_optimizer.py"
    ).read_text(encoding="utf-8")
    rcm_sensor_source = (
        COMPONENT / "rcm_sensor.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "expected_natural_headroom_kwh",
        "planned_grid_discharge_kwh",
        "pre_discharge_target_soc_percent",
        "pre_discharge_power_percent",
        "pre_discharge_ready",
        "protected_minimum_soc",
        "export_capacity_kw > 0.1",
    ):
        require(
            marker in rcm_optimizer_source,
            f"RCEm morning-discharge safety marker missing: {marker}",
        )
    require(
        "sensor.hoymiles_hit_maximum_discharge_current" in rcm_sensor_source
        and "minutes_to_risk" in rcm_sensor_source
        and "risk_day_offset" in rcm_sensor_source,
        "RCEm sensor lacks BMS/time inputs for safe morning discharge",
    )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    github_release_notes = (
        ROOT / "docs" / "releases" / f"v{manifest['version']}.md"
    ).read_text(encoding="utf-8")
    release_match = re.search(
        rf"^## \[{re.escape(manifest['version'])}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        re.M | re.S,
    )
    require(
        release_match is not None,
        f"CHANGELOG lacks the {manifest['version']} release section",
    )
    release_notes = release_match.group(1)
    require(
        "### User update steps / Kroki po aktualizacji" in release_notes,
        "Release changelog lacks the HACS-visible user update steps",
    )
    for update_step in (
        "1. **HACS:**",
        "2. **Home Assistant:**",
        "3. **ESP32 / ESPHome:**",
        "4. **Verification / Weryfikacja:**",
    ):
        require(
            update_step in release_notes,
            f"Release changelog lacks required user step: {update_step}",
        )
    release_procedure = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
    require(
        "GitHub Release body visible in HACS" in release_procedure
        and "does not flash the ESP32" in release_procedure,
        "Release procedure does not require complete HACS/ESP32 instructions",
    )
    readme_images = [ROOT / "docs" / "images" / "dashboard-overview.png"]
    for image in readme_images:
        require(image.is_file(), f"Missing README image: {image.relative_to(ROOT)}")
        require(
            str(image.relative_to(ROOT)).replace("\\", "/") in readme,
            f"README does not reference {image.name}",
        )
        width, height = png_dimensions(image)
        require(
            width >= 1000 and height >= 700,
            f"README image is unexpectedly small: {image.name}",
        )
    require(
        "docs/QUICK_START.md" in readme
        and (ROOT / "docs" / "QUICK_START.md").is_file(),
        "README does not expose the beginner quick-start guide",
    )
    require(
        "## User update steps / Kroki po aktualizacji" in github_release_notes
        and "ESP32 / ESPHome" in github_release_notes
        and "2064/2064" in github_release_notes,
        "GitHub Release notes are incomplete for HACS users",
    )
    for documentation_marker in (
        "Nie tylko pokazuje. Myśli.",
        "Local EMS for Home Assistant",
        "/releases/latest",
        "More than Modbus monitoring",
        "Safety boundary / Granica bezpieczeństwa",
        "Tariff-aware grid charging",
        "RCEm 253 V+ voltage management",
        "LiFePO4 storage balancing",
        "docs/AUTOMATION_TEST_REPORT.md",
    ):
        require(
            documentation_marker in readme,
            f"README is missing release documentation: {documentation_marker}",
        )

    esphome_entry_files = [
        ROOT / "hoymiles-inverter.yaml",
        ROOT / "examples" / "esphome" / "hoymiles-hit-g3.yaml",
    ]
    required_esphome_packages = {
        f"packages/{path.name}"
        for path in (ROOT / "packages").glob("*.yaml")
        if not path.name.startswith("optional_")
    }
    for entry_file in esphome_entry_files:
        entry_text = entry_file.read_text(encoding="utf-8")
        require(
            "!include packages/" not in entry_text,
            f"Public ESPHome entry point uses local includes: {entry_file.name}",
        )
        require(
            "url: https://github.com/Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus"
            in entry_text,
            f"Public ESPHome entry point has no remote package: {entry_file.name}",
        )
        require(
            "ref: v1.4.4" in entry_text,
            f"ESPHome entry point is not pinned to v1.4.4: {entry_file.name}",
        )
        require(
            "dashboard_import:" in entry_text
            and "package_import_url: github://Kaluzaburza/"
            "Hoymiles_HIT_xxL_G3_ModBus/" in entry_text
            and "import_full_config: true" in entry_text,
            f"ESPHome adoption/update metadata is missing in {entry_file.name}",
        )
        included_packages = set(
            re.findall(r"^\s*-\s+(packages/[a-z0-9_]+\.yaml)\s*$", entry_text, re.M)
        )
        require(
            included_packages == required_esphome_packages,
            f"ESPHome package list differs in {entry_file.name}",
        )
        require(
            entry_text.index("- packages/parallel_network.yaml")
            < entry_text.index("- packages/settings.yaml"),
            f"Parallel topology must load before EMS settings in {entry_file.name}",
        )

    parallel_source = (ROOT / "packages" / "parallel_network.yaml").read_text(
        encoding="utf-8"
    )
    settings_source = (ROOT / "packages" / "settings.yaml").read_text(
        encoding="utf-8"
    )
    for marker in (
        "address: 6048",
        "address: 6049",
        'name: "Parallel Topology"',
        'name: "Parallel EMS Control Status"',
        "count < 2 || count > 10",
        "Gotowe - Master steruje siecią równoległą",
    ):
        require(
            marker in parallel_source,
            f"Parallel topology marker missing: {marker}",
        )
    for marker in (
        "machine_type == 2",
        "machine_count < 2 || machine_count > 10",
        "0x00, 0x10, 0x10, 0xCC, 0x00, 0x07, 0x0E",
        "id(modbus_1).send_raw(payload);",
        "poza kolejką ModbusController oczekującą na odpowiedź",
    ):
        require(
            marker in settings_source,
            f"Parallel EMS safety marker missing: {marker}",
        )
    require(
        "hoymiles_modbus_slave_" not in parallel_source
        and "hoymiles_modbus_slave_" not in settings_source,
        "External Modbus must not poll or write internal parallel Slave addresses",
    )

    for asset in required_assets[:4]:
        text = asset.read_text(encoding="utf-8")
        require(
            not legacy_entity_pattern.search(text),
            f"Installation-specific entity id remains in {asset.name}",
        )
        require(
            not re.search(r"\bPV[56]\b", text, flags=re.IGNORECASE),
            f"Unsupported PV5/PV6 reference remains in {asset.name}",
        )

    polish_dashboard = required_assets[1].read_text(encoding="utf-8")
    english_dashboard = required_assets[0].read_text(encoding="utf-8")
    for dashboard_text, language in (
        (polish_dashboard, "Polish"),
        (english_dashboard, "English"),
    ):
        dashboard_lines = dashboard_text.splitlines()
        require(
            not re.search(
                r"^\s*-\s+(?:button|sensor|number|select)\.hoymiles_hit_[a-z0-9_]+\s*$",
                dashboard_text,
                re.M,
            ),
            f"{language} dashboard contains entity rows without short names",
        )
        for index, line in enumerate(dashboard_lines):
            entity_match = re.match(
                r"^(?P<indent>\s*)-\s+entity:\s+"
                r"(?:button|sensor|number|select)\.hoymiles_hit_[a-z0-9_]+\s*$",
                line,
            )
            if not entity_match:
                continue
            following = (
                dashboard_lines[index + 1]
                if index + 1 < len(dashboard_lines)
                else ""
            )
            require(
                following.startswith(
                    f"{entity_match.group('indent')}  name:"
                ),
                f"{language} dashboard entity row on line {index + 1} "
                "has no dashboard-only short name",
            )
        require(
            not re.search(
                r"^\s*name:\s*[\"']?Hoymiles Inverter\b",
                dashboard_text,
                re.M | re.I,
            ),
            f"{language} dashboard still displays the Hoymiles Inverter prefix",
        )
        require(
            "<table>" not in dashboard_text,
            f"{language} dashboard still contains non-clickable HTML tables",
        )
        require(
            "decimal_places: 2" in dashboard_text,
            f"{language} dashboard does not show power flow with two decimals",
        )
        require(
            "sensor.hoymiles_hit_battery_current_inverter" in dashboard_text,
            f"{language} dashboard does not show inverter-side battery current",
        )
        require(
            "sensor.hoymiles_hit_battery_1_voltage" in dashboard_text,
            f"{language} dashboard does not show inverter-side battery voltage",
        )
        require(
            "button.hoymiles_hit_clear_fault" in dashboard_text,
            f"{language} dashboard lacks the Clear Fault button",
        )
        require(
            '<ha-alert alert-type="error">' not in dashboard_text,
            f"{language} dashboard still contains the removed red alert rows",
        )
        state_title = (
            "Stan systemu i łączność"
            if language == "Polish"
            else "System and connectivity"
        )
        alarm_title = (
            "Alarmy — szybki podgląd"
            if language == "Polish"
            else "Alarms — quick view"
        )
        require(
            f"- type: custom:hoymiles-zebra-entities-card\n        title: {state_title}"
            in dashboard_text
            and f"- type: custom:hoymiles-zebra-entities-card\n        title: {alarm_title}"
            in dashboard_text,
            f"{language} dashboard status/alarm cards are not clickable entity cards",
        )
        require(
            dashboard_text.count("action: more-info") >= 32,
            f"{language} dashboard status/alarm rows do not explicitly open more-info history",
        )
        require(
            f"path: {'sterowanie' if language == 'Polish' else 'control'}\n    icon: mdi:tune-variant\n    type: sidebar"
            in dashboard_text,
            f"{language} control view is not using the sidebar layout",
        )
        for entity_id in (
            "input_boolean.hoymiles_ems_push_notifications_enabled",
            "input_text.hoymiles_ems_push_notify_target",
            "sensor.hoymiles_ems_push_notification_status",
            "input_boolean.hoymiles_rce_dynamic_soc_enabled",
            "input_number.hoymiles_rce_soc_safety_margin",
            "input_text.hoymiles_solcast_forecast_today_entity",
            "input_text.hoymiles_solcast_forecast_tomorrow_entity",
            "input_select.hoymiles_rce_inverter_rated_power",
            "input_number.hoymiles_rce_fallback_daily_load",
            "sensor.hoymiles_hit_rce_optimized_plan",
            "sensor.hoymiles_solcast_forecast_today",
            "sensor.hoymiles_solcast_forecast_remaining_today",
            "sensor.hoymiles_solcast_forecast_tomorrow",
            "sensor.hoymiles_load_average_4_days",
            "sensor.hoymiles_night_load_average_4_days",
            "sensor.hoymiles_rce_protected_home_energy",
            "sensor.hoymiles_rce_dynamic_minimum_soc",
            "sensor.hoymiles_rce_self_use_baseline_export",
            "sensor.hoymiles_hit_grid_energy_sell_today",
            "sensor.hoymiles_rce_realized_controlled_export_today",
            "sensor.hoymiles_rce_realized_natural_export_today",
            "sensor.hoymiles_rce_unclassified_export_today",
            "sensor.hoymiles_rce_realized_controlled_revenue_today",
            "sensor.hoymiles_rce_realized_natural_revenue_today",
            "sensor.hoymiles_rce_realized_revenue_today",
            "sensor.hoymiles_rce_day",
            "sensor.hoymiles_rce_day_tomorrow",
            "input_boolean.hoymiles_tariff_charge_enabled",
            "input_select.hoymiles_tariff_type",
            "input_number.hoymiles_tariff_g11_price",
            "input_number.hoymiles_tariff_low_price",
            "sensor.hoymiles_hit_tariff_charge_plan",
            "binary_sensor.hoymiles_tariff_planned_charge_slot",
            "sensor.hoymiles_tariff_target_soc",
            "sensor.hoymiles_tariff_planned_grid_import",
            "sensor.hoymiles_tariff_estimated_savings",
            "sensor.hoymiles_tariff_grid_charge_energy_daily",
            "sensor.hoymiles_tariff_savings_daily",
            "input_boolean.hoymiles_battery_balancing_enabled",
            "input_number.hoymiles_battery_balancing_interval_days",
            "input_number.hoymiles_battery_balancing_hold_hours",
            "sensor.hoymiles_battery_balancing_status",
            "sensor.hoymiles_battery_balancing_next_run",
            "timer.hoymiles_battery_balancing_hold",
            "sensor.hoymiles_battery_balancing_slow_charge_power",
        ):
            require(
                entity_id in dashboard_text,
                f"{language} dashboard lacks dynamic RCE entity {entity_id}",
            )
        require(
            "path: ladowanie-taryfowe" in dashboard_text,
            f"{language} dashboard lacks the tariff charging view",
        )
        require(
            "https://github.com/BJReplay/ha-solcast-solar" in dashboard_text,
            f"{language} dashboard does not document the Solcast dependency",
        )
    require(
        "Wyczyść alarmy falownika" in polish_dashboard,
        "Polish dashboard lacks the localized Clear Fault name",
    )
    require(
        "Clear Fault" in english_dashboard,
        "English dashboard lacks the localized Clear Fault name",
    )
    require(
        'name: "Docelowy SOC ładowania z sieci"' in polish_dashboard,
        "Polish dashboard lacks the localized Force Charge SOC name",
    )
    require(
        'name: "Maksymalna moc rozładowania do sieci"' in polish_dashboard,
        "Polish dashboard lacks the localized Maximum Discharge Power name",
    )
    require(
        "title: Sieć — napięcia i częstotliwość" in polish_dashboard
        and "title: Odbiór — moc" in polish_dashboard,
        "Polish dashboard does not split live grid voltage and load power into two cards",
    )
    require(
        "title: Grid — voltages and frequency" in english_dashboard
        and "title: Loads — power" in english_dashboard,
        "English dashboard does not split live grid voltage and load power into two cards",
    )
    require(
        "potrzebna domowi" not in english_dashboard,
        "English dashboard contains an untranslated RCE reserve explanation",
    )
    for dashboard_text, language in (
        (polish_dashboard, "Polish"),
        (english_dashboard, "English"),
    ):
        for entity_id in (
            "sensor.hoymiles_hit_load_power_l1n",
            "sensor.hoymiles_hit_load_power_l2n",
            "sensor.hoymiles_hit_load_power_l3n",
            "sensor.hoymiles_hit_load_power_total",
        ):
            require(
                entity_id in dashboard_text,
                f"{language} dashboard lacks live load-power entity {entity_id}",
            )
        for entity_id in (
            "sensor.hoymiles_rce_revenue_daily",
            "sensor.hoymiles_rce_revenue_weekly",
            "sensor.hoymiles_rce_revenue_monthly",
            "sensor.hoymiles_rce_revenue_yearly",
            "sensor.hoymiles_rce_grid_export_energy_daily",
            "sensor.hoymiles_rce_grid_export_energy_weekly",
            "sensor.hoymiles_rce_grid_export_energy_monthly",
            "sensor.hoymiles_rce_grid_export_energy_yearly",
        ):
            require(
                entity_id in dashboard_text,
                f"{language} dashboard lacks profit-period entity {entity_id}",
            )
        for entity_id in (
            "select.hoymiles_hit_generation_control_function",
            "number.hoymiles_hit_maximum_export_power_limit",
            "select.hoymiles_hit_gen_port_mode",
            "sensor.hoymiles_hit_overview_generator_active_power",
        ):
            require(
                entity_id in dashboard_text,
                f"{language} dashboard lacks GCF/GEN entity {entity_id}",
            )
        require(
            "grid_input_power_limitation_valley" not in dashboard_text,
            f"{language} dashboard still contains the removed Valley setting",
        )
    require(
        'id: replenish_power_310' in settings_source
        and 'max_value: 1000' in settings_source
        and 'return x > 1000.0f ? 1000.0f' in settings_source,
        "Low-SOC grid-charge register 310 is not protected by the 1000 W limit",
    )
    for register_id in (
        "battery_max_charge_power_306",
        "battery_max_discharge_power_307",
    ):
        register_offset = settings_source.index(f"id: {register_id}")
        register_block = settings_source[register_offset : register_offset + 650]
        require(
            "lambda: return x * 0.1f;" in register_block
            and "return safe * 10.0f;" in register_block,
            f"Register {register_id} does not use the required 0.1% scale",
        )
    for dashboard_text, language, topology_name, status_name in (
        (
            polish_dashboard,
            "Polish",
            'name: "Topologia sieci"',
            'name: "Gotowość sterowania EMS"',
        ),
        (
            english_dashboard,
            "English",
            'name: "Network topology"',
            'name: "EMS control readiness"',
        ),
    ):
        require(
            "sensor.hoymiles_hit_parallel_topology" in dashboard_text
            and "sensor.hoymiles_hit_parallel_ems_control_status" in dashboard_text
            and topology_name in dashboard_text
            and status_name in dashboard_text,
            f"{language} dashboard lacks localized parallel EMS diagnostics",
        )

    english_assets = [required_assets[0], required_assets[2]]
    polish_characters = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
    for asset in english_assets:
        visible_lines = [
            line
            for line in asset.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        visible_text = "\n".join(visible_lines).replace(
            "sensor.solcast_pv_forecast_prognoza_na_jutro",
            "sensor.solcast_pv_forecast_localized_tomorrow",
        )
        require(
            not polish_characters.search(visible_text),
            f"Visible Polish text remains in English asset {asset.name}",
        )

    for python_file in COMPONENT.glob("*.py"):
        py_compile.compile(python_file, doraise=True)

    localization = load_localization_module()
    require(
        localization.localized_text_state("Praca z siecią", "en") == "On-grid operation",
        "English text state localization failed",
    )
    require(
        localization.localized_text_state("Praca z siecią", "pl") == "Praca z siecią",
        "Polish text state localization failed",
    )
    require(
        localization.localized_text_state("Brak błędu", "en") == "No error",
        "English fault state localization failed",
    )
    require(
        localization.localized_text_state(
            "Gotowe - Master steruje siecią równoległą", "en"
        )
        == "Ready - Master controls the parallel network",
        "English parallel EMS state localization failed",
    )
    require(
        localization.localized_text_state(
            "Zablokowane - ESP32 podłączone do Slave", "pl"
        )
        == "Zablokowane - ESP32 podłączone do Slave",
        "Polish parallel EMS state localization failed",
    )
    validate_fresh_asset_install()

    for image_name in (
        "icon.png",
        "dark_icon.png",
        "icon@2x.png",
        "dark_icon@2x.png",
        "logo.png",
        "dark_logo.png",
    ):
        image_path = COMPONENT / "brand" / image_name
        require(image_path.is_file(), f"Missing brand asset {image_name}")
        width, height = png_dimensions(image_path)
        require(width >= 128 and height >= 128, f"{image_name} is too small")
    for image_name, expected_size in (
        ("icon.png", 256),
        ("dark_icon.png", 256),
        ("icon@2x.png", 512),
        ("dark_icon@2x.png", 512),
    ):
        width, height = png_dimensions(COMPONENT / "brand" / image_name)
        require(
            (width, height) == (expected_size, expected_size),
            f"{image_name} must be {expected_size}x{expected_size}",
        )

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    license_policy = (ROOT / "LICENSE_POLICY.md").read_text(encoding="utf-8")
    notice_text = (ROOT / "NOTICE").read_text(encoding="utf-8")
    contribution_text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    pr_template_text = (
        ROOT / ".github" / "pull_request_template.md"
    ).read_text(encoding="utf-8")
    codeowners_text = (ROOT / ".github" / "CODEOWNERS").read_text(
        encoding="utf-8"
    )
    workflow_text = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    require(
        license_text.startswith("MIT License\n\nCopyright (c) 2026 Kaluzaburza")
        and "Permission is hereby granted, free of charge" in license_text
        and 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text,
        "MIT license text is missing or incomplete",
    )
    require(
        hashlib.sha256(
            (ROOT / "LICENSE").read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        == "fa0bb01cef85cd8e77d7930efae10d9c93812cfa6e4878f3d7d57ad23c224290",
        "LICENSE is not the reviewed MIT text",
    )
    require(
        "[MIT License](LICENSE)" in license_policy
        and "Private and commercial use" in license_policy
        and "Oprogramowanie jest udostępniane bez gwarancji" in license_policy
        and "PolyForm" not in license_policy,
        "License policy does not describe the current MIT terms cleanly",
    )
    require(
        notice_text.startswith("Copyright (c) 2026 Kaluzaburza")
        and "Licensed under the MIT License" in notice_text,
        "Required copyright notice is missing",
    )
    require(
        "[MIT License](LICENSE)" in contribution_text
        and "commercial purposes" in contribution_text
        and "Contribution Certificate 1.0" in contribution_text
        and "Signed-off-by:" in contribution_text,
        "Contribution rights and sign-off terms are incomplete",
    )
    require(
        "I accept [CONTRIBUTING.md]" in pr_template_text
        and "Signed-off-by:" in pr_template_text
        and "* @Kaluzaburza" in codeowners_text,
        "Pull-request rights confirmation or CODEOWNERS is missing",
    )
    for test_command in (
        "python tools/test_tariff_profiles.py",
        "python tools/test_tariff_optimizer.py",
        "python tools/test_rcm_history.py",
        "python tools/test_rcm_optimizer.py",
        "python tools/test_automation_matrix.py",
        "python tools/test_automation_matrix.py --exhaustive",
    ):
        require(test_command in workflow_text, f"CI does not run {test_command}")
    require(
        "validate-hacs:" in workflow_text
        and "HACS validation" in workflow_text
        and "continue-on-error" not in workflow_text
        and "ignore:" not in workflow_text,
        "Official HACS validation must be mandatory and unignored",
    )

    print(f"HACS layout: OK ({len(integration_dirs)} integration)")
    print(f"Manifest: OK (version {manifest['version']})")
    print(f"Localized entities: {len(catalog)} (English and Polish)")
    print("Bundled dashboards/EMS assets: OK")
    print("HACS-visible user update instructions: OK")
    print("README screenshots: OK")
    print("Public ESPHome remote packages: OK")
    print("Python syntax: OK")
    print("Text-state localization: OK")
    print("Fresh PL/EN asset installation: OK")
    print("Brand assets: OK")
    print("MIT/OSI license and current license documentation: OK")
    print("Contribution rights, sign-off and CODEOWNERS: OK")
    print("RCE/tariff/RCEm CI regression matrix: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

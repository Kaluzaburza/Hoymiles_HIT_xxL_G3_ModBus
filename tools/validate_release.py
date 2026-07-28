"""Structural release validation without requiring a Home Assistant checkout."""

from __future__ import annotations

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
    core.HomeAssistant = object
    homeassistant.core = core
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.core", core)

    path = COMPONENT / "assets.py"
    spec = importlib.util.spec_from_file_location("hoymiles_assets", path)
    require(spec is not None and spec.loader is not None, "Cannot load assets")
    module = importlib.util.module_from_spec(spec)
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
    require(manifest["version"] == "1.2.0", "Release version must be 1.2.0")

    entity_source = (COMPONENT / "entity.py").read_text(encoding="utf-8")
    init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    require(
        "def suggested_object_id(self)" in entity_source,
        "Proxy entities need an explicit stable suggested_object_id property",
    )
    require(
        "_async_migrate_entity_ids(hass, entry)" in init_source,
        "Existing localized entity ids are not migrated",
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
        RESOURCES / "www" / "hoymiles-inverter.png",
    ]
    for asset in required_assets:
        require(asset.is_file(), f"Missing bundled asset: {asset.relative_to(ROOT)}")

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
                (domain, translation_key) in identities,
                f"Asset references an entity absent from the catalog: "
                f"{domain}.hoymiles_hit_{translation_key}",
            )

    for package_path in required_assets[2:4]:
        package_text = package_path.read_text(encoding="utf-8")
        dynamic_reserve_markers = (
            "input_boolean.hoymiles_rce_dynamic_soc_enabled",
            "input_number.hoymiles_rce_soc_safety_margin",
            "input_text.hoymiles_solcast_forecast_tomorrow_entity",
            "sensor.solcast_pv_forecast_forecast_tomorrow",
            "sensor.solcast_pv_forecast_prognoza_na_jutro",
            "state_characteristic: sum_differences_nonnegative",
            "max_age:\n      days: 4",
            "sensor.hoymiles_hit_load_energy_use_today",
            "sensor.hoymiles_hit_overview_load_active_power",
            "sensor.hoymiles_night_protected_load_power",
            "sensor.hoymiles_night_protection_window_remaining",
            "sensor.hoymiles_protected_window_expected_load",
            "{% set upcoming_start = setting - buffer %}",
            "{% set upcoming_end = rising + buffer %}",
            "upcoming_end - upcoming_start",
            "sensor.hoymiles_rce_protected_home_energy",
            "[night, 0] | max + [deficit, 0] | max",
            "state_attr('sun.sun', 'next_rising')",
            "state_attr('sun.sun', 'next_setting')",
            "reserve + (protected / capacity * 100) + margin",
            "sensor.hoymiles_hit_battery_capacity",
            "number.hoymiles_hit_self_use_soc",
            "sensor.hoymiles_rce_dynamic_minimum_soc",
            "sensor.hoymiles_rce_effective_minimum_soc",
            "binary_sensor.hoymiles_rce_reserve_ready",
            "or is_state('binary_sensor.hoymiles_rce_reserve_ready', 'off')",
        )
        for marker in dynamic_reserve_markers:
            require(
                marker in package_text,
                f"Dynamic RCE reserve marker missing in {package_path.name}: {marker}",
            )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_images = [
        ROOT / "docs" / "images" / "dashboard-energy-flow.png",
        ROOT / "docs" / "images" / "dashboard-rce-automation.png",
    ]
    for image in readme_images:
        require(image.is_file(), f"Missing README image: {image.relative_to(ROOT)}")
        require(
            str(image.relative_to(ROOT)).replace("\\", "/") in readme,
            f"README does not reference {image.name}",
        )
        width, height = png_dimensions(image)
        require(
            width >= 1200 and height >= 700,
            f"README image is unexpectedly small: {image.name}",
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
        "Master rozsyła EMS i dispatch",
        "Master steruje siecią",
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
            f"- type: entities\n        title: {state_title}" in dashboard_text
            and f"- type: entities\n        title: {alarm_title}" in dashboard_text,
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
            "input_boolean.hoymiles_rce_dynamic_soc_enabled",
            "input_number.hoymiles_rce_soc_safety_margin",
            "input_text.hoymiles_solcast_forecast_tomorrow_entity",
            "sensor.hoymiles_solcast_forecast_tomorrow",
            "sensor.hoymiles_load_average_4_days",
            "binary_sensor.hoymiles_night_protection_window",
            "sensor.hoymiles_night_protection_window_duration",
            "sensor.hoymiles_night_protection_window_remaining",
            "sensor.hoymiles_night_load_average_4_days",
            "sensor.hoymiles_protected_window_expected_load",
            "sensor.hoymiles_rce_energy_deficit_tomorrow",
            "sensor.hoymiles_rce_protected_home_energy",
            "sensor.hoymiles_rce_dynamic_minimum_soc",
            "sensor.hoymiles_rce_effective_minimum_soc",
            "binary_sensor.hoymiles_rce_reserve_ready",
        ):
            require(
                entity_id in dashboard_text,
                f"{language} dashboard lacks dynamic RCE entity {entity_id}",
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
        and "title: Sieć — moc" in polish_dashboard,
        "Polish dashboard does not split live grid voltage and power into two cards",
    )
    require(
        "title: Grid — voltages and frequency" in english_dashboard
        and "title: Grid — power" in english_dashboard,
        "English dashboard does not split live grid voltage and power into two cards",
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
            "sensor.hoymiles_hit_meter_grid_active_power_l1",
            "sensor.hoymiles_hit_meter_grid_active_power_l2",
            "sensor.hoymiles_hit_meter_grid_active_power_l3",
            "sensor.hoymiles_hit_meter_grid_total_active_power",
        ):
            require(
                entity_id in dashboard_text,
                f"{language} dashboard lacks live grid power entity {entity_id}",
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

    for image_name in ("icon.png", "dark_icon.png", "logo.png", "dark_logo.png"):
        image_path = COMPONENT / "brand" / image_name
        require(image_path.is_file(), f"Missing brand asset {image_name}")
        width, height = png_dimensions(image_path)
        require(width >= 128 and height >= 128, f"{image_name} is too small")

    print(f"HACS layout: OK ({len(integration_dirs)} integration)")
    print(f"Manifest: OK (version {manifest['version']})")
    print(f"Localized entities: {len(catalog)} (English and Polish)")
    print("Bundled dashboards/EMS assets: OK")
    print("README screenshots: OK")
    print("Public ESPHome remote packages: OK")
    print("Python syntax: OK")
    print("Text-state localization: OK")
    print("Fresh PL/EN asset installation: OK")
    print("Brand assets: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Standalone privacy and structure tests for support diagnostics."""

from __future__ import annotations

import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import types
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hoymiles_hit_modbus"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_component_module(module_name: str, filename: str):
    package_name = "hoymiles_hit_modbus_test"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(COMPONENT)]
        sys.modules[package_name] = package
    full_name = f"{package_name}.{module_name}"
    path = COMPONENT / filename
    spec = importlib.util.spec_from_file_location(full_name, path)
    require(spec is not None and spec.loader is not None, f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    redaction = load_component_module(
        "diagnostic_redaction",
        "diagnostic_redaction.py",
    )
    payload = {
        "voltage": 252.4,
        "planned_slots": ["18:00", "18:30"],
        "api_key": "this-must-never-be-exported",
        "encryption_key": "another-private-key",
        "wifi_ssid": "Private network",
        "note": (
            "host 192.168.8.106, AA:BB:CC:DD:EE:FF, "
            "owner@example.com, password=short-secret and "
            "https://private.example/path?token=abc"
        ),
        "nested": {"refresh_token": "secret-token", "soc": 72},
    }
    cleaned = redaction.sanitize_diagnostic_value(payload)
    serialized = repr(cleaned)
    for forbidden in (
        "this-must-never-be-exported",
        "another-private-key",
        "Private network",
        "192.168.8.106",
        "AA:BB:CC:DD:EE:FF",
        "owner@example.com",
        "short-secret",
        "private.example",
        "secret-token",
    ):
        require(forbidden not in serialized, f"Sensitive value leaked: {forbidden}")
    require(cleaned["voltage"] == 252.4, "Numeric telemetry was changed")
    require(cleaned["nested"]["soc"] == 72, "SOC telemetry was changed")
    require(cleaned["planned_slots"] == ["18:00", "18:30"], "Plan changed")

    bundle = load_component_module("diagnostic_bundle", "diagnostic_bundle.py")
    with tempfile.TemporaryDirectory(prefix="hoymiles_diagnostics_test_") as tmp:
        log_path = Path(tmp) / "home-assistant.log"
        log_path.write_text(
            "unrelated component message\n"
            "[E][hoymiles] Modbus timeout host=192.168.8.106 "
            "password=short-secret\n",
            encoding="utf-8",
        )
        archive_bytes = bundle.build_support_archive(
            [{"soc": 72, "api_key": "this-must-never-be-exported"}],
            log_path=log_path,
            generated_at="2026-08-09T12:00:00+00:00",
            home_assistant_version="2026.8.0",
        )
    with ZipFile(BytesIO(archive_bytes)) as archive:
        require(
            set(archive.namelist())
            == {
                "README.txt",
                "environment.json",
                "hoymiles_diagnostics.json",
                "home_assistant_relevant_logs.txt",
            },
            "Browser ZIP has an unexpected structure",
        )
        archive_text = "\n".join(
            archive.read(name).decode("utf-8") for name in archive.namelist()
        )
    require("Modbus timeout" in archive_text, "Relevant Core log was omitted")
    require("unrelated component" not in archive_text, "Unrelated log leaked")
    for forbidden in (
        "192.168.8.106",
        "short-secret",
        "this-must-never-be-exported",
    ):
        require(forbidden not in archive_text, f"ZIP leaked: {forbidden}")

    script = (COMPONENT / "collect_diagnostics.sh").read_text(encoding="utf-8")
    require("secrets.yaml" not in script, "Collector must not read secrets.yaml")
    require(
        "cp /config/.storage" not in script,
        "Collector must not copy the Home Assistant storage database",
    )
    require("[REDACTED_SECRET]" in script, "Shell secret masking is missing")
    require("native_diagnostics_" in script, "Native report collection is missing")
    support_source = (COMPONENT / "support_http.py").read_text(encoding="utf-8")
    for expected in (
        'user.is_admin',
        'content_type="application/zip"',
        '"Cache-Control": "no-store"',
        'build_support_archive',
    ):
        require(expected in support_source, f"HTTP ZIP endpoint is missing: {expected}")

    card_type = "custom:hoymiles-diagnostics-download-card"
    for language in ("pl", "en"):
        dashboard = json.loads(
            (
                COMPONENT
                / "resources"
                / "www"
                / f"dashboard_hoymiles_{language}.json"
            ).read_text(encoding="utf-8")
        )
        diagnostic_view = next(
            view for view in dashboard["views"] if view.get("path") == "diagnostyka"
        )
        require(
            diagnostic_view["cards"][0].get("type") == card_type,
            f"{language} dashboard does not start Diagnostics with ZIP download",
        )
    card_source = (ROOT / "home_assistant" / "www" / "hoymiles-rce-chart-card.js")
    bundled_card = COMPONENT / "resources" / "www" / "hoymiles-rce-chart-card.js"
    require(
        card_source.read_bytes() == bundled_card.read_bytes(),
        "Bundled diagnostics card differs from its source",
    )
    print("Diagnostics privacy and browser ZIP tests passed")


if __name__ == "__main__":
    main()

"""Standalone privacy and structure tests for support diagnostics."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hoymiles_hit_modbus"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_redaction_module():
    path = COMPONENT / "diagnostic_redaction.py"
    spec = importlib.util.spec_from_file_location("diagnostic_redaction", path)
    require(spec is not None and spec.loader is not None, "Cannot load redaction")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    redaction = load_redaction_module()
    payload = {
        "voltage": 252.4,
        "planned_slots": ["18:00", "18:30"],
        "api_key": "this-must-never-be-exported",
        "encryption_key": "another-private-key",
        "wifi_ssid": "Private network",
        "note": (
            "host 192.168.8.106, AA:BB:CC:DD:EE:FF, "
            "owner@example.com and https://private.example/path?token=abc"
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
        "private.example",
        "secret-token",
    ):
        require(forbidden not in serialized, f"Sensitive value leaked: {forbidden}")
    require(cleaned["voltage"] == 252.4, "Numeric telemetry was changed")
    require(cleaned["nested"]["soc"] == 72, "SOC telemetry was changed")
    require(cleaned["planned_slots"] == ["18:00", "18:30"], "Plan changed")

    script = (COMPONENT / "collect_diagnostics.sh").read_text(encoding="utf-8")
    require("secrets.yaml" not in script, "Collector must not read secrets.yaml")
    require(
        "cp /config/.storage" not in script,
        "Collector must not copy the Home Assistant storage database",
    )
    require("[REDACTED_SECRET]" in script, "Shell secret masking is missing")
    require("native_diagnostics_" in script, "Native report collection is missing")
    print("Diagnostics privacy tests passed")


if __name__ == "__main__":
    main()

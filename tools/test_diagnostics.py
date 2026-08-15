"""Standalone privacy and structure tests for support diagnostics."""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import types
from uuid import RFC_4122, UUID
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hoymiles_hit_modbus"


class FakeStore:
    """Persistent in-memory replacement for Home Assistant Store."""

    backend: dict[str, dict] = {}
    load_count = 0
    save_count = 0
    fail_saves = False
    initialized: list[tuple[int, str]] = []

    def __init__(self, _hass, version: int, key: str) -> None:
        self.version = version
        self.key = key
        self.initialized.append((version, key))

    @classmethod
    def reset(cls) -> None:
        cls.backend = {}
        cls.load_count = 0
        cls.save_count = 0
        cls.fail_saves = False
        cls.initialized = []

    async def async_load(self):
        await asyncio.sleep(0)
        type(self).load_count += 1
        return deepcopy(type(self).backend.get(self.key))

    async def async_save(self, data) -> None:
        await asyncio.sleep(0)
        if type(self).fail_saves:
            raise OSError("simulated persistent storage failure")
        type(self).save_count += 1
        type(self).backend[self.key] = deepcopy(data)


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


def load_installation_identity_module():
    """Load the identity helper with a behavioral Store stub."""
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")
    core.HomeAssistant = object
    storage.Store = FakeStore
    homeassistant.core = core
    homeassistant.helpers = helpers
    helpers.storage = storage
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.storage"] = storage

    package_name = "hoymiles_hit_modbus_test"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(COMPONENT)]
        sys.modules[package_name] = package
    const = types.ModuleType(f"{package_name}.const")
    const.DOMAIN = "hoymiles_hit_modbus"
    sys.modules[const.__name__] = const
    return load_component_module(
        "installation_identity",
        "installation_identity.py",
    )


async def assert_installation_identity_contract(
    identity_module,
) -> tuple[dict, dict]:
    """Prove generation, persistence, privacy and single-flight behavior."""
    expected_uuid = UUID("3f6f8b4e-7793-4f4b-9f45-486ddf65f78a")
    uuid_calls: list[tuple[tuple, dict]] = []

    def fake_uuid4(*args, **kwargs):
        uuid_calls.append((args, kwargs))
        return expected_uuid

    identity_module.uuid4 = fake_uuid4
    FakeStore.reset()
    private_markers = (
        "AA:BB:CC:DD:EE:FF",
        "192.168.8.106",
        "private-host",
        "device-id-private",
        "entry-id-private",
        "user-id-private",
        "serial-private",
    )

    def fake_hass():
        return types.SimpleNamespace(
            data={
                "device_id": private_markers[3],
                "entry_id": private_markers[4],
                "user_id": private_markers[5],
                "serial": private_markers[6],
                "network": [private_markers[0], private_markers[1]],
            },
            config=types.SimpleNamespace(
                location_name=private_markers[2],
            ),
        )

    first_hass = fake_hass()
    first = await identity_module.async_get_or_create_installation_identity(
        first_hass
    )
    first_payload = first.as_dict()
    require(
        first.anonymous_installation_id == str(expected_uuid),
        "Generated identity is not the direct output of uuid4()",
    )
    require(FakeStore.save_count == 1, "First start did not save identity once")
    require(len(uuid_calls) == 1, "First start did not generate exactly one UUID")
    require(
        uuid_calls[0] == ((), {}),
        "UUID factory received installation-derived arguments",
    )
    require(
        set(first_payload)
        == {
            "anonymous_installation_id",
            "installation_id_schema_version",
        },
        "Store payload contains fields outside the anonymous identity contract",
    )
    require(
        first_payload["installation_id_schema_version"] == 1,
        "Installation identity schema is not version 1",
    )
    require(
        FakeStore.backend[identity_module.INSTALLATION_ID_STORAGE_KEY]
        == first_payload,
        "Persisted identity differs from the published identity",
    )
    require(
        FakeStore.initialized[-1]
        == (1, "hoymiles_hit_modbus.installation_identity"),
        "Identity Store key is not installation-wide and stable",
    )

    # A new hass object simulates a full Home Assistant restart while Store
    # retains its on-disk data.
    restarted_hass = fake_hass()
    restarted = (
        await identity_module.async_get_or_create_installation_identity(
            restarted_hass
        )
    )
    require(
        restarted == first,
        "Home Assistant restart changed the anonymous installation ID",
    )
    require(FakeStore.save_count == 1, "Restart rewrote the persistent ID")
    require(len(uuid_calls) == 1, "Restart generated a replacement UUID")

    parsed = UUID(first.anonymous_installation_id)
    require(parsed.version == 4, "Installation ID is not UUID v4")
    require(parsed.variant == RFC_4122, "Installation ID is not RFC 4122")
    require(
        str(parsed) == first.anonymous_installation_id,
        "Installation ID is not in canonical UUID form",
    )
    serialized_identity = json.dumps(first_payload)
    for marker in private_markers:
        require(
            marker not in serialized_identity,
            f"Installation-derived value leaked into anonymous ID: {marker}",
        )

    # Multiple config entries call the same installation-wide getter and must
    # never receive per-device IDs.
    entry_a = await identity_module.async_get_or_create_installation_identity(
        restarted_hass
    )
    entry_b = await identity_module.async_get_or_create_installation_identity(
        restarted_hass
    )
    require(entry_a == entry_b == first, "Config entries received different IDs")

    # Concurrent first requests must serialize load/create/save and publish
    # only the one successfully persisted value.
    FakeStore.reset()
    uuid_calls.clear()
    concurrent_hass = fake_hass()
    concurrent = await asyncio.gather(
        *(
            identity_module.async_get_or_create_installation_identity(
                concurrent_hass
            )
            for _ in range(12)
        )
    )
    require(len(set(concurrent)) == 1, "Concurrent requests produced multiple IDs")
    require(FakeStore.save_count == 1, "Concurrent requests saved more than once")
    require(len(uuid_calls) == 1, "Concurrent requests generated multiple UUIDs")

    # Invalid persisted data must not be exported as the anonymous ID.
    FakeStore.reset()
    FakeStore.backend[identity_module.INSTALLATION_ID_STORAGE_KEY] = {
        "anonymous_installation_id": private_markers[6],
        "installation_id_schema_version": 1,
    }
    uuid_calls.clear()
    repaired = await identity_module.async_get_or_create_installation_identity(
        fake_hass()
    )
    require(repaired == first, "Invalid stored ID was not replaced by UUID v4")
    require(FakeStore.save_count == 1, "Invalid stored ID was not persisted safely")
    require(len(uuid_calls) == 1, "Invalid stored ID did not regenerate once")

    # JSON booleans and floats compare equal to integer 1 in Python, but are
    # not valid schema-version integers and must be repaired, never exported.
    for invalid_schema in (True, 1.0):
        FakeStore.reset()
        FakeStore.backend[identity_module.INSTALLATION_ID_STORAGE_KEY] = {
            "anonymous_installation_id": str(expected_uuid),
            "installation_id_schema_version": invalid_schema,
        }
        uuid_calls.clear()
        repaired_schema = (
            await identity_module.async_get_or_create_installation_identity(
                fake_hass()
            )
        )
        require(
            repaired_schema.installation_id_schema_version == 1
            and type(repaired_schema.installation_id_schema_version) is int,
            f"Invalid schema type was exported: {invalid_schema!r}",
        )
        require(
            FakeStore.save_count == 1 and len(uuid_calls) == 1,
            f"Invalid schema type was not regenerated: {invalid_schema!r}",
        )

    # A future schema must never be silently downgraded or overwritten.
    FakeStore.reset()
    future_payload = {
        "anonymous_installation_id": str(expected_uuid),
        "installation_id_schema_version": 2,
    }
    FakeStore.backend[identity_module.INSTALLATION_ID_STORAGE_KEY] = deepcopy(
        future_payload
    )
    uuid_calls.clear()
    try:
        await identity_module.async_get_or_create_installation_identity(
            fake_hass()
        )
    except identity_module.UnsupportedInstallationIdentitySchemaError:
        pass
    else:
        raise RuntimeError("Unknown future identity schema was accepted")
    require(FakeStore.save_count == 0, "Future identity schema was overwritten")
    require(len(uuid_calls) == 0, "Future identity schema generated a new UUID")
    require(
        FakeStore.backend[identity_module.INSTALLATION_ID_STORAGE_KEY]
        == future_payload,
        "Future identity schema changed on downgrade",
    )

    # A failed persistent write must not publish/cache an ephemeral identity.
    # A later retry must perform a fresh generation and persist it successfully.
    FakeStore.reset()
    FakeStore.fail_saves = True
    uuid_calls.clear()
    failing_hass = fake_hass()
    try:
        await identity_module.async_get_or_create_installation_identity(
            failing_hass
        )
    except OSError as err:
        require(
            str(err) == "simulated persistent storage failure",
            "Unexpected storage error escaped the identity getter",
        )
    else:
        raise RuntimeError("Failed Store write published an ephemeral identity")
    require(
        identity_module._DATA_IDENTITY not in failing_hass.data,
        "Unsaved anonymous ID entered the Home Assistant cache",
    )
    require(FakeStore.save_count == 0, "Failed Store write counted as persisted")
    require(
        identity_module.INSTALLATION_ID_STORAGE_KEY not in FakeStore.backend,
        "Failed Store write changed persistent data",
    )
    require(len(uuid_calls) == 1, "Failed first write did not generate once")

    FakeStore.fail_saves = False
    retried = await identity_module.async_get_or_create_installation_identity(
        failing_hass
    )
    require(retried == first, "Storage recovery published a different UUID value")
    require(FakeStore.save_count == 1, "Storage recovery did not persist identity")
    require(len(uuid_calls) == 2, "Storage recovery reused an unsaved UUID")
    return first_payload, restarted.as_dict()


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

    identity_module = load_installation_identity_module()
    identity, restarted_identity = asyncio.run(
        assert_installation_identity_contract(identity_module)
    )
    preserved = redaction.sanitize_diagnostic_value(identity)
    require(
        preserved["anonymous_installation_id"]
        == identity["anonymous_installation_id"],
        "Redaction removed the intentional anonymous installation ID",
    )
    rejected = redaction.sanitize_diagnostic_value(
        {"anonymous_installation_id": "serial-private"}
    )
    require(
        rejected["anonymous_installation_id"] == redaction.REDACTED,
        "Invalid data bypassed redaction through the anonymous ID field",
    )

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
            [
                {
                    **identity,
                    "report_name": "entry-a",
                    "soc": 72,
                    "api_key": "this-must-never-be-exported",
                },
                {
                    **identity,
                    "report_name": "entry-b",
                    "soc": 68,
                    # The bundle's installation-wide identity is authoritative
                    # even if a future caller accidentally supplies a mismatch.
                    "anonymous_installation_id": (
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                    ),
                    "installation_id_schema_version": 99,
                },
            ],
            log_path=log_path,
            generated_at="2026-08-09T12:00:00+00:00",
            home_assistant_version="2026.8.0",
            **identity,
        )
        second_archive_bytes = bundle.build_support_archive(
            [
                {
                    **restarted_identity,
                    "report_name": "entry-a",
                    "soc": 71,
                }
            ],
            log_path=log_path,
            generated_at="2026-08-10T12:00:00+00:00",
            home_assistant_version="2026.8.1",
            **restarted_identity,
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
        environment = json.loads(archive.read("environment.json"))
        reports = json.loads(archive.read("hoymiles_diagnostics.json"))
    with ZipFile(BytesIO(second_archive_bytes)) as second_archive:
        second_environment = json.loads(
            second_archive.read("environment.json")
        )
        second_reports = json.loads(
            second_archive.read("hoymiles_diagnostics.json")
        )
    for exported in [environment, second_environment, *reports, *second_reports]:
        require(
            exported["anonymous_installation_id"]
            == identity["anonymous_installation_id"],
            "Two diagnostics archives or config entries changed the ID",
        )
        require(
            exported["installation_id_schema_version"] == 1,
            "ZIP omitted the installation ID schema version",
        )
    require("Modbus timeout" in archive_text, "Relevant Core log was omitted")
    require("info@kaluzaaa.com" in archive_text, "Support email is missing from ZIP")
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
        'async_get_or_create_installation_identity',
    ):
        require(expected in support_source, f"HTTP ZIP endpoint is missing: {expected}")

    diagnostics_source = (COMPONENT / "diagnostics.py").read_text(
        encoding="utf-8"
    )
    for expected in (
        "await async_get_or_create_installation_identity(",
        "**installation_identity.as_dict()",
        '"sensor.hoymiles_ems_hardware_mode"',
        '"sensor.hoymiles_parallel_aggregate_physical_response"',
        "AGGREGATE_RESPONSE_HISTORY_ATTRIBUTE_KEYS",
        "regular_query = partial(",
        "response_query = partial(",
        '"sampled_transition_peak_kw"',
    ):
        require(
            expected in diagnostics_source,
            f"Native diagnostics report is missing identity wiring: {expected}",
        )
    diagnostics_tree = ast.parse(diagnostics_source)
    response_attribute_keys: set[str] | None = None
    for statement in diagnostics_tree.body:
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "AGGREGATE_RESPONSE_HISTORY_ATTRIBUTE_KEYS"
                for target in statement.targets
            )
            and isinstance(statement.value, ast.Call)
            and statement.value.args
        ):
            response_attribute_keys = set(
                ast.literal_eval(statement.value.args[0])
            )
            break
    require(
        response_attribute_keys
        == {
            "authoritative_expected_power",
            "baseline_generation",
            "candidate_generations",
            "collection_baseline_generation",
            "completed_at",
            "configuration_acknowledgement_scope",
            "detected_inverters",
            "evidence_scope",
            "expected_power_kw",
            "final_generation",
            "formula",
            "grid_samples_kw",
            "individual_inverter_acknowledgement",
            "latched_machine_type",
            "observed_median_power_kw",
            "observed_spread_kw",
            "owner",
            "pending_at",
            "phase",
            "reason",
            "required_stable_generations",
            "requires_parallel_proof",
            "sample_count",
            "sampled_transition_observed",
            "sampled_transition_peak_kw",
            "sampled_transition_scope",
            "samples_kw",
            "stable_window_start",
            "tolerance_kw",
            "topology_known",
            "transaction_id",
            "transaction_started_epoch",
            "transition_grace_seconds",
            "verification_horizon_seconds",
        },
        "Recorder response-attribute allowlist diverged from the frozen contract",
    )

    setup_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    identity_init = setup_source.index(
        "await async_get_or_create_installation_identity(hass)"
    )
    asset_init = setup_source.index("await _async_prepare_frontend_assets(hass)")
    require(
        identity_init < asset_init,
        "Installation identity is not initialized before optional assets",
    )

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
    require(
        b"info@kaluzaaa.com" in card_source.read_bytes(),
        "Diagnostic card does not show the support email",
    )
    print("Diagnostics privacy and browser ZIP tests passed")


if __name__ == "__main__":
    main()

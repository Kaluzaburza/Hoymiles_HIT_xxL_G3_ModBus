"""Build downloadable, privacy-filtered Hoymiles support archives."""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from .diagnostic_redaction import sanitize_diagnostic_value


MAX_LOG_BYTES = 2_000_000
MAX_LOG_LINES = 2_500
LOG_PATTERN = re.compile(
    r"hoymiles|esphome|modbus|rce|rcm|tariff|taryf|ems|"
    r"SocketClosedAPIError",
    re.IGNORECASE,
)


def _relevant_log_lines(log_path: Path) -> str:
    """Return the relevant tail of a Core log without loading an unbounded file."""
    if not log_path.is_file():
        return "Home Assistant log file is not available in /config.\n"
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, 2)
            size = log_file.tell()
            log_file.seek(max(size - MAX_LOG_BYTES, 0))
            raw = log_file.read(MAX_LOG_BYTES)
    except OSError as err:
        return f"Cannot read Home Assistant log ({type(err).__name__}).\n"

    text = raw.decode("utf-8", errors="replace")
    if size > MAX_LOG_BYTES:
        # The first line may start in the middle after seeking into a large log.
        text = text.partition("\n")[2]
    relevant = [line for line in text.splitlines() if LOG_PATTERN.search(line)]
    sanitized = sanitize_diagnostic_value(relevant[-MAX_LOG_LINES:])
    if not sanitized:
        return "No relevant Hoymiles/ESPHome/Modbus log lines were found.\n"
    return "\n".join(str(line) for line in sanitized) + "\n"


def build_support_archive(
    reports: Iterable[dict[str, Any]],
    *,
    log_path: Path,
    generated_at: str,
    home_assistant_version: str,
    anonymous_installation_id: str,
    installation_id_schema_version: int,
) -> bytes:
    """Return a ZIP with diagnostic JSON and redacted relevant Core logs."""
    reports_with_identity = [
        {
            **report,
            "anonymous_installation_id": anonymous_installation_id,
            "installation_id_schema_version": installation_id_schema_version,
        }
        for report in reports
    ]
    safe_reports = sanitize_diagnostic_value(reports_with_identity)
    metadata = {
        "generated_at": generated_at,
        "home_assistant_version": home_assistant_version,
        "report_count": len(safe_reports),
        "anonymous_installation_id": anonymous_installation_id,
        "installation_id_schema_version": installation_id_schema_version,
    }
    safe_metadata = sanitize_diagnostic_value(metadata)
    readme = (
        "EMS for Hoymiles HIT-(5–20)L-G3 diagnostic archive\n\n"
        "Attach this ZIP together with the exact local date/time of the fault "
        "and a short description of the expected behaviour. Send the complete "
        "report to info@kaluzaaa.com.\n"
        "The archive contains the current integration state, 24 hours of "
        "significant control history and relevant Home Assistant Core logs.\n"
        "ESPHome device runtime logs are not exposed to Home Assistant Core; "
        "for low-level UART/Modbus faults attach an ESPHome log excerpt too.\n\n"
        "Paczka diagnostyczna EMS for Hoymiles HIT-(5–20)L-G3\n\n"
        "Dołącz ZIP wraz z dokładną lokalną datą i godziną błędu oraz opisem "
        "oczekiwanego zachowania i wyślij całość na info@kaluzaaa.com. "
        "Paczka zawiera bieżący stan integracji, "
        "24 godziny istotnych zmian sterowania i powiązane logi HA Core.\n"
        "Logi pracy urządzenia ESPHome nie są udostępniane procesowi HA Core; "
        "przy błędach UART/Modbus dołącz również fragment logu ESPHome.\n\n"
        "Device, account and configuration identifiers are automatically "
        "masked. A random anonymous installation ID is intentionally kept "
        "only to correlate support archives from this Home Assistant over "
        "time. "
        "Review the files before posting them publicly.\n"
        "Identyfikatory urządzeń, kont i konfiguracji są automatycznie "
        "maskowane. Losowy anonimowy identyfikator instalacji pozostaje "
        "wyłącznie do łączenia kolejnych paczek wsparcia z tego Home "
        "Assistanta. "
        "Przejrzyj pliki przed ich publicznym udostępnieniem.\n"
    )

    archive_buffer = BytesIO()
    with ZipFile(archive_buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr(
            "environment.json",
            json.dumps(safe_metadata, ensure_ascii=False, indent=2) + "\n",
        )
        archive.writestr(
            "hoymiles_diagnostics.json",
            json.dumps(safe_reports, ensure_ascii=False, indent=2) + "\n",
        )
        archive.writestr(
            "home_assistant_relevant_logs.txt",
            _relevant_log_lines(log_path),
        )
    return archive_buffer.getvalue()

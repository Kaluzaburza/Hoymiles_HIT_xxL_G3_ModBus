"""Bounded, extraction-free reader for Hoymiles diagnostic ZIP archives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import struct
from typing import Any
from uuid import RFC_4122, UUID
import zlib
from zipfile import (
    BadZipFile,
    ZIP_DEFLATED,
    ZIP_STORED,
    ZipFile,
    ZipInfo,
)

from .models import (
    ArchiveMetadata,
    DiagnosticReport,
    LoadedDiagnosticArchive,
    SUPPORTED_INSTALLATION_ID_SCHEMA_VERSIONS,
    SUPPORTED_REPORT_SCHEMA_VERSIONS,
)


ENVIRONMENT_MEMBER = "environment.json"
DIAGNOSTICS_MEMBER = "hoymiles_diagnostics.json"
LOG_MEMBER = "home_assistant_relevant_logs.txt"
README_MEMBER = "README.txt"
REQUIRED_MEMBERS = frozenset({ENVIRONMENT_MEMBER, DIAGNOSTICS_MEMBER})
KNOWN_MEMBERS = frozenset(
    {ENVIRONMENT_MEMBER, DIAGNOSTICS_MEMBER, LOG_MEMBER, README_MEMBER}
)
ALLOWED_COMPRESSION = frozenset({ZIP_STORED, ZIP_DEFLATED})
READ_CHUNK_BYTES = 64 * 1024
EOCD_SIGNATURE = b"PK\x05\x06"
EOCD_STRUCT = struct.Struct("<4s4H2LH")
MAX_EOCD_SEARCH_BYTES = EOCD_STRUCT.size + 65_535


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Hard resource bounds applied before and during archive parsing."""

    max_archives: int = 100
    max_archive_bytes: int = 64 * 1024 * 1024
    max_members: int = 32
    max_central_directory_bytes: int = 512 * 1024
    max_reports: int = 64
    max_environment_bytes: int = 256 * 1024
    max_diagnostics_bytes: int = 48 * 1024 * 1024
    max_log_bytes: int = 4 * 1024 * 1024
    max_total_uncompressed_bytes: int = 64 * 1024 * 1024
    max_compression_ratio: float = 250.0
    compression_ratio_minimum_bytes: int = 1024 * 1024
    max_json_depth: int = 64
    max_json_nodes: int = 500_000
    max_json_string_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        """Reject disabled or internally inconsistent safety limits."""

        integer_limits = (
            self.max_archives,
            self.max_archive_bytes,
            self.max_members,
            self.max_central_directory_bytes,
            self.max_reports,
            self.max_environment_bytes,
            self.max_diagnostics_bytes,
            self.max_log_bytes,
            self.max_total_uncompressed_bytes,
            self.compression_ratio_minimum_bytes,
            self.max_json_depth,
            self.max_json_nodes,
            self.max_json_string_bytes,
        )
        if any(value <= 0 for value in integer_limits):
            raise ValueError("Archive limits must be positive")
        if not math.isfinite(self.max_compression_ratio) or (
            self.max_compression_ratio <= 1.0
        ):
            raise ValueError("Compression-ratio limit must be finite and above 1")


DEFAULT_LIMITS = ArchiveLimits()


class ArchiveReadError(ValueError):
    """Expected rejection of an unsafe or malformed diagnostic archive."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class _DuplicateJsonKeyError(ValueError):
    """Internal marker for ambiguous JSON objects."""


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(READ_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as err:
        raise ArchiveReadError(
            "ARCHIVE_READ_FAILED",
            f"Cannot read archive ({type(err).__name__})",
            path=path,
        ) from err
    return digest.hexdigest()


def _preflight_central_directory(
    path: Path,
    *,
    source_size: int,
    limits: ArchiveLimits,
) -> None:
    """Bound the ZIP central directory before ``ZipFile`` materializes it."""

    read_size = min(source_size, MAX_EOCD_SEARCH_BYTES)
    try:
        with path.open("rb") as source:
            source.seek(source_size - read_size)
            tail = source.read(read_size)
    except OSError as err:
        raise ArchiveReadError(
            "ARCHIVE_READ_FAILED",
            f"Cannot read ZIP footer ({type(err).__name__})",
            path=path,
        ) from err
    offset = tail.rfind(EOCD_SIGNATURE)
    if offset < 0 or len(tail) - offset < EOCD_STRUCT.size:
        raise ArchiveReadError(
            "ZIP_EOCD_MISSING",
            "ZIP end-of-central-directory record is missing",
            path=path,
        )
    (
        _signature,
        disk_number,
        directory_disk,
        entries_on_disk,
        total_entries,
        directory_size,
        directory_offset,
        comment_length,
    ) = EOCD_STRUCT.unpack_from(tail, offset)
    if offset + EOCD_STRUCT.size + comment_length != len(tail):
        raise ArchiveReadError(
            "ZIP_EOCD_INVALID",
            "ZIP footer length is inconsistent",
            path=path,
        )
    if disk_number != 0 or directory_disk != 0 or entries_on_disk != total_entries:
        raise ArchiveReadError(
            "ZIP_MULTIDISK_UNSUPPORTED",
            "Multi-disk ZIP archives are not accepted",
            path=path,
        )
    if (
        total_entries == 0xFFFF
        or directory_size == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
    ):
        raise ArchiveReadError(
            "ZIP64_UNSUPPORTED",
            "ZIP64 diagnostic archives are not accepted",
            path=path,
        )
    if total_entries > limits.max_members:
        raise ArchiveReadError(
            "ZIP_TOO_MANY_MEMBERS",
            f"ZIP has more than {limits.max_members} members",
            path=path,
        )
    if directory_size > limits.max_central_directory_bytes:
        raise ArchiveReadError(
            "ZIP_CENTRAL_DIRECTORY_LIMIT",
            "ZIP central directory exceeds the safety limit",
            path=path,
        )
    absolute_eocd_offset = source_size - read_size + offset
    if directory_offset + directory_size > absolute_eocd_offset:
        raise ArchiveReadError(
            "ZIP_CENTRAL_DIRECTORY_INVALID",
            "ZIP central-directory bounds are inconsistent",
            path=path,
        )


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def discover_archives(
    inputs: Iterable[str | Path],
    *,
    recursive: bool = True,
    limits: ArchiveLimits = DEFAULT_LIMITS,
) -> tuple[Path, ...]:
    """Return deterministic, unique ZIP paths without following symlink dirs."""

    candidates: dict[str, Path] = {}

    def remember(candidate: Path) -> None:
        resolved = candidate.resolve()
        key = _path_key(resolved)
        if key in candidates:
            return
        if len(candidates) >= limits.max_archives:
            raise ArchiveReadError(
                "TOO_MANY_ARCHIVES",
                f"Archive count exceeds the limit of {limits.max_archives}",
            )
        candidates[key] = resolved

    saw_input = False
    for raw_input in inputs:
        saw_input = True
        source = Path(raw_input).expanduser()
        if not source.exists():
            raise ArchiveReadError(
                "INPUT_NOT_FOUND",
                "An input path does not exist",
                path=source,
            )
        if source.is_symlink():
            raise ArchiveReadError(
                "SYMLINK_INPUT_REJECTED",
                "Symlink inputs are not followed",
                path=source,
            )
        if source.is_file():
            if source.suffix.casefold() != ".zip":
                raise ArchiveReadError(
                    "UNSUPPORTED_ARCHIVE_TYPE",
                    "Only diagnostic ZIP files are supported",
                    path=source,
                )
            remember(source)
        elif source.is_dir():
            if recursive:
                for directory, directory_names, file_names in os.walk(
                    source,
                    followlinks=False,
                ):
                    directory_names[:] = [
                        name
                        for name in directory_names
                        if not (Path(directory) / name).is_symlink()
                    ]
                    for filename in file_names:
                        if not filename.casefold().endswith(".zip"):
                            continue
                        candidate = Path(directory) / filename
                        if candidate.is_symlink() or not candidate.is_file():
                            continue
                        remember(candidate)
            else:
                for candidate in source.iterdir():
                    if candidate.suffix.casefold() != ".zip":
                        continue
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    remember(candidate)
        else:
            raise ArchiveReadError(
                "UNSUPPORTED_INPUT",
                "Input is neither a regular file nor a directory",
                path=source,
            )
    if not saw_input:
        raise ArchiveReadError("NO_INPUTS", "No input paths were provided")
    return tuple(sorted(candidates.values(), key=_path_key))


def _member_path_is_suspicious(filename: str) -> bool:
    if not filename or "\x00" in filename or "\\" in filename:
        return True
    path = PurePosixPath(filename)
    return (
        path.is_absolute()
        or bool(path.parts and path.parts[0].endswith(":"))
        or any(part in {"", ".", ".."} for part in path.parts)
    )


def _validate_members(
    archive: ZipFile,
    *,
    limits: ArchiveLimits,
    path: Path,
) -> tuple[dict[str, ZipInfo], tuple[str, ...]]:
    infos = archive.infolist()
    if len(infos) > limits.max_members:
        raise ArchiveReadError(
            "ZIP_TOO_MANY_MEMBERS",
            f"ZIP has more than {limits.max_members} members",
            path=path,
        )
    members: dict[str, ZipInfo] = {}
    total_uncompressed = 0
    for info in infos:
        if _member_path_is_suspicious(info.filename):
            raise ArchiveReadError(
                "ZIP_SUSPICIOUS_MEMBER_PATH",
                "ZIP contains a suspicious member path",
                path=path,
            )
        if info.filename in members:
            raise ArchiveReadError(
                "ZIP_DUPLICATE_MEMBER",
                "ZIP contains duplicate member names",
                path=path,
            )
        members[info.filename] = info
        unix_mode = info.external_attr >> 16
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise ArchiveReadError(
                "ZIP_SYMLINK_REJECTED",
                "ZIP symlink members are not accepted",
                path=path,
            )
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise ArchiveReadError(
                "ZIP_ENCRYPTED_MEMBER",
                "Encrypted ZIP members are not accepted",
                path=path,
            )
        if info.compress_type not in ALLOWED_COMPRESSION:
            raise ArchiveReadError(
                "ZIP_UNSUPPORTED_COMPRESSION",
                "ZIP member uses unsupported compression",
                path=path,
            )
        if info.file_size < 0 or info.compress_size < 0:
            raise ArchiveReadError(
                "ZIP_INVALID_MEMBER_SIZE",
                "ZIP member has an invalid size",
                path=path,
            )
        total_uncompressed += info.file_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            raise ArchiveReadError(
                "ZIP_UNCOMPRESSED_LIMIT",
                "ZIP declared uncompressed size exceeds the safety limit",
                path=path,
            )
        if info.file_size >= limits.compression_ratio_minimum_bytes:
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > limits.max_compression_ratio:
                raise ArchiveReadError(
                    "ZIP_COMPRESSION_RATIO_LIMIT",
                    "ZIP member compression ratio exceeds the safety limit",
                    path=path,
                )
    missing = REQUIRED_MEMBERS - members.keys()
    if missing:
        raise ArchiveReadError(
            "ZIP_REQUIRED_MEMBER_MISSING",
            "ZIP is missing a required diagnostics member",
            path=path,
        )
    extra = tuple(sorted(set(members) - KNOWN_MEMBERS))
    return members, extra


def _read_member(
    archive: ZipFile,
    info: ZipInfo,
    *,
    maximum_bytes: int,
    total_read: list[int],
    limits: ArchiveLimits,
    path: Path,
) -> bytes:
    if info.file_size > maximum_bytes:
        raise ArchiveReadError(
            "ZIP_MEMBER_SIZE_LIMIT",
            "A diagnostic member exceeds its size limit",
            path=path,
        )
    chunks: list[bytes] = []
    member_read = 0
    try:
        with archive.open(info, "r") as source:
            while chunk := source.read(READ_CHUNK_BYTES):
                member_read += len(chunk)
                total_read[0] += len(chunk)
                if member_read > maximum_bytes:
                    raise ArchiveReadError(
                        "ZIP_MEMBER_SIZE_LIMIT",
                        "A diagnostic member exceeded its streaming size limit",
                        path=path,
                    )
                if total_read[0] > limits.max_total_uncompressed_bytes:
                    raise ArchiveReadError(
                        "ZIP_UNCOMPRESSED_LIMIT",
                        "Total decompressed data exceeded the safety limit",
                        path=path,
                    )
                chunks.append(chunk)
    except ArchiveReadError:
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError, zlib.error) as err:
        raise ArchiveReadError(
            "ZIP_MEMBER_READ_FAILED",
            f"Cannot read ZIP member ({type(err).__name__})",
            path=path,
        ) from err
    if member_read != info.file_size:
        raise ArchiveReadError(
            "ZIP_MEMBER_SIZE_MISMATCH",
            "ZIP member size differs from its central-directory declaration",
            path=path,
        )
    return b"".join(chunks)


def _validate_json_shape(
    value: Any,
    *,
    limits: ArchiveLimits,
    path: Path,
) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_json_nodes:
            raise ArchiveReadError(
                "JSON_NODE_LIMIT",
                "JSON contains too many values",
                path=path,
            )
        if depth > limits.max_json_depth:
            raise ArchiveReadError(
                "JSON_DEPTH_LIMIT",
                "JSON nesting exceeds the safety limit",
                path=path,
            )
        if isinstance(current, str):
            try:
                encoded_length = len(current.encode("utf-8"))
            except UnicodeEncodeError as err:
                raise ArchiveReadError(
                    "JSON_INVALID_UNICODE",
                    "JSON contains an invalid Unicode surrogate",
                    path=path,
                ) from err
            if encoded_length > limits.max_json_string_bytes:
                raise ArchiveReadError(
                    "JSON_STRING_LIMIT",
                    "JSON contains an oversized string",
                    path=path,
                )
        elif isinstance(current, float) and not math.isfinite(current):
            raise ArchiveReadError(
                "JSON_NON_FINITE_NUMBER",
                "JSON contains a non-finite number",
                path=path,
            )
        elif isinstance(current, Mapping):
            for key, child in current.items():
                if not isinstance(key, str):  # json invariant, kept explicit
                    raise ArchiveReadError(
                        "JSON_NON_STRING_KEY",
                        "JSON object contains a non-string key",
                        path=path,
                    )
                try:
                    encoded_key_length = len(key.encode("utf-8"))
                except UnicodeEncodeError as err:
                    raise ArchiveReadError(
                        "JSON_INVALID_UNICODE",
                        "JSON contains an invalid Unicode surrogate in a key",
                        path=path,
                    ) from err
                if encoded_key_length > limits.max_json_string_bytes:
                    raise ArchiveReadError(
                        "JSON_STRING_LIMIT",
                        "JSON contains an oversized object key",
                        path=path,
                    )
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def _load_json_member(
    archive: ZipFile,
    info: ZipInfo,
    *,
    maximum_bytes: int,
    total_read: list[int],
    limits: ArchiveLimits,
    path: Path,
) -> Any:
    raw = _read_member(
        archive,
        info,
        maximum_bytes=maximum_bytes,
        total_read=total_read,
        limits=limits,
        path=path,
    )
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateJsonKeyError as err:
        raise ArchiveReadError(
            "JSON_DUPLICATE_KEY",
            "JSON contains a duplicate object key",
            path=path,
        ) from err
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as err:
        raise ArchiveReadError(
            "JSON_INVALID",
            f"Cannot parse diagnostic JSON ({type(err).__name__})",
            path=path,
        ) from err
    _validate_json_shape(value, limits=limits, path=path)
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _strict_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _strict_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _valid_uuid4(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    if (
        parsed.version != 4
        or parsed.variant != RFC_4122
        or str(parsed) != value
    ):
        return None
    return value


def _resolve_installation_identity(
    environment: Mapping[str, Any],
    reports: list[Mapping[str, Any]],
    *,
    content_sha256: str,
) -> tuple[str | None, int | None, str, list[str]]:
    sources: list[Mapping[str, Any]] = [environment, *reports]
    supplied = [
        (
            source.get("anonymous_installation_id"),
            source.get("installation_id_schema_version"),
        )
        for source in sources
        if "anonymous_installation_id" in source
        or "installation_id_schema_version" in source
    ]
    if not supplied:
        return (
            None,
            None,
            f"unlinked-{content_sha256[:20]}",
            ["INSTALLATION_ID_MISSING"],
        )

    normalized: list[tuple[str, int]] = []
    for raw_id, raw_schema in supplied:
        identifier = _valid_uuid4(raw_id)
        schema = _strict_int(raw_schema)
        if (
            identifier is None
            or schema not in SUPPORTED_INSTALLATION_ID_SCHEMA_VERSIONS
        ):
            return (
                None,
                None,
                f"unlinked-{content_sha256[:20]}",
                ["INSTALLATION_ID_INVALID"],
            )
        normalized.append((identifier, schema))
    if len(set(normalized)) != 1:
        return (
            None,
            None,
            f"unlinked-{content_sha256[:20]}",
            ["INSTALLATION_ID_MISMATCH"],
        )
    identifier, schema = normalized[0]
    grouping_digest = sha256(f"{schema}:{identifier}".encode("ascii")).hexdigest()
    return identifier, schema, f"inst-{grouping_digest[:20]}", []


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reports_from_json(
    value: Any,
    *,
    warnings: list[str],
    limits: ArchiveLimits,
    path: Path,
) -> tuple[tuple[DiagnosticReport, ...], list[Mapping[str, Any]]]:
    if not isinstance(value, list) or not value:
        raise ArchiveReadError(
            "DIAGNOSTICS_ROOT_INVALID",
            "hoymiles_diagnostics.json must contain a non-empty report list",
            path=path,
        )
    if len(value) > limits.max_reports:
        raise ArchiveReadError(
            "DIAGNOSTIC_REPORT_COUNT_LIMIT",
            f"Diagnostic report count exceeds the limit of {limits.max_reports}",
            path=path,
        )
    reports: list[DiagnosticReport] = []
    raw_reports: list[Mapping[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ArchiveReadError(
                "DIAGNOSTIC_REPORT_INVALID",
                "A diagnostic report is not a JSON object",
                path=path,
            )
        report_schema = _strict_int(raw.get("report_schema"))
        if report_schema is None:
            warnings.append("REPORT_SCHEMA_MISSING_OR_INVALID")
        elif report_schema not in SUPPORTED_REPORT_SCHEMA_VERSIONS:
            warnings.append("REPORT_SCHEMA_UNSUPPORTED")
        generated_at = _parse_datetime(raw.get("generated_at"))
        if raw.get("generated_at") is not None and generated_at is None:
            warnings.append("REPORT_TIMESTAMP_INVALID")
        catalog_entities_value = raw.get("catalog_entities")
        catalog_entities = (
            tuple(
                item
                for item in catalog_entities_value
                if isinstance(item, Mapping)
            )
            if isinstance(catalog_entities_value, list)
            else ()
        )
        if isinstance(catalog_entities_value, list) and (
            len(catalog_entities) != len(catalog_entities_value)
        ):
            warnings.append("CATALOG_ENTITY_INVALID")
        raw_reports.append(raw)
        reports.append(
            DiagnosticReport(
                report_index=index,
                report_schema_version=report_schema,
                generated_at=generated_at,
                integration_version=_strict_string(
                    raw.get("integration_version")
                ),
                managed_state_snapshot=_mapping_or_empty(
                    raw.get("managed_state_snapshot")
                ),
                control_history=_mapping_or_empty(raw.get("control_history")),
                catalog_entities=catalog_entities,
                catalog_coverage=_mapping_or_empty(raw.get("catalog_coverage")),
                raw=raw,
            )
        )
    return tuple(reports), raw_reports


def load_diagnostic_archive(
    path: str | Path,
    *,
    limits: ArchiveLimits = DEFAULT_LIMITS,
) -> LoadedDiagnosticArchive:
    """Load one bounded diagnostic ZIP without extracting files to disk."""

    archive_path = Path(path).expanduser()
    if archive_path.is_symlink():
        raise ArchiveReadError(
            "SYMLINK_INPUT_REJECTED",
            "Symlink inputs are not followed",
            path=archive_path,
        )
    try:
        source_size = archive_path.stat().st_size
    except OSError as err:
        raise ArchiveReadError(
            "ARCHIVE_STAT_FAILED",
            f"Cannot inspect archive ({type(err).__name__})",
            path=archive_path,
        ) from err
    if not archive_path.is_file():
        raise ArchiveReadError(
            "ARCHIVE_NOT_FILE",
            "Diagnostic archive is not a regular file",
            path=archive_path,
        )
    if source_size > limits.max_archive_bytes:
        raise ArchiveReadError(
            "ARCHIVE_SIZE_LIMIT",
            f"Archive exceeds the {limits.max_archive_bytes}-byte limit",
            path=archive_path,
        )
    _preflight_central_directory(
        archive_path,
        source_size=source_size,
        limits=limits,
    )
    content_hash = _sha256_file(archive_path)
    warnings: list[str] = []
    try:
        with ZipFile(archive_path, "r") as archive:
            members, extra_members = _validate_members(
                archive,
                limits=limits,
                path=archive_path,
            )
            if extra_members:
                warnings.append("ZIP_EXTRA_MEMBERS_IGNORED")
            total_read = [0]
            environment_value = _load_json_member(
                archive,
                members[ENVIRONMENT_MEMBER],
                maximum_bytes=limits.max_environment_bytes,
                total_read=total_read,
                limits=limits,
                path=archive_path,
            )
            diagnostics_value = _load_json_member(
                archive,
                members[DIAGNOSTICS_MEMBER],
                maximum_bytes=limits.max_diagnostics_bytes,
                total_read=total_read,
                limits=limits,
                path=archive_path,
            )
            relevant_log_text: str | None = None
            if LOG_MEMBER in members:
                raw_log = _read_member(
                    archive,
                    members[LOG_MEMBER],
                    maximum_bytes=limits.max_log_bytes,
                    total_read=total_read,
                    limits=limits,
                    path=archive_path,
                )
                relevant_log_text = raw_log.decode("utf-8", errors="replace")
    except ArchiveReadError:
        raise
    except (BadZipFile, OSError, RuntimeError, UnicodeError, ValueError) as err:
        raise ArchiveReadError(
            "ZIP_INVALID",
            f"Cannot open diagnostic ZIP ({type(err).__name__})",
            path=archive_path,
        ) from err

    if not isinstance(environment_value, Mapping):
        raise ArchiveReadError(
            "ENVIRONMENT_ROOT_INVALID",
            "environment.json must contain a JSON object",
            path=archive_path,
        )
    reports, raw_reports = _reports_from_json(
        diagnostics_value,
        warnings=warnings,
        limits=limits,
        path=archive_path,
    )
    declared_report_count = _strict_int(environment_value.get("report_count"))
    if declared_report_count is None:
        warnings.append("REPORT_COUNT_MISSING_OR_INVALID")
    elif declared_report_count != len(reports):
        warnings.append("REPORT_COUNT_MISMATCH")
    generated_at = _parse_datetime(environment_value.get("generated_at"))
    if generated_at is None:
        warnings.append(
            "ARCHIVE_TIMESTAMP_INVALID"
            if environment_value.get("generated_at") is not None
            else "ARCHIVE_TIMESTAMP_MISSING"
        )
        report_timestamps = [
            report.generated_at
            for report in reports
            if report.generated_at is not None
        ]
        if report_timestamps:
            generated_at = max(report_timestamps)
            warnings.append("ARCHIVE_TIMESTAMP_FROM_REPORT")
    (
        anonymous_installation_id,
        installation_id_schema_version,
        installation_key,
        identity_warnings,
    ) = _resolve_installation_identity(
        environment_value,
        raw_reports,
        content_sha256=content_hash,
    )
    warnings.extend(identity_warnings)
    report_schema_versions = tuple(
        sorted(
            {
                report.report_schema_version
                for report in reports
                if report.report_schema_version is not None
            }
        )
    )
    metadata = ArchiveMetadata(
        archive_key=f"archive-{content_hash[:20]}",
        content_sha256=content_hash,
        source_size_bytes=source_size,
        generated_at=generated_at,
        home_assistant_version=_strict_string(
            environment_value.get("home_assistant_version")
        ),
        declared_report_count=declared_report_count,
        actual_report_count=len(reports),
        anonymous_installation_id=anonymous_installation_id,
        installation_id_schema_version=installation_id_schema_version,
        installation_key=installation_key,
        report_schema_versions=report_schema_versions,
        extra_members=extra_members,
    )
    return LoadedDiagnosticArchive(
        metadata=metadata,
        reports=reports,
        environment=environment_value,
        relevant_log_text=relevant_log_text,
        warnings=tuple(dict.fromkeys(warnings)),
    )

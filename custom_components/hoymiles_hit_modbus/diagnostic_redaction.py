"""Privacy helpers shared by Hoymiles diagnostic reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
import math
import re
from typing import Any
from uuid import RFC_4122, UUID


REDACTED = "[REDACTED]"
MAX_DEPTH = 8
MAX_ITEMS = 500
MAX_STRING_LENGTH = 8_000

_SENSITIVE_KEY_PARTS = (
    "access_key",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "device_id",
    "email",
    "entry_id",
    "friendly_name",
    "host",
    "latitude",
    "location",
    "longitude",
    "mac",
    "name_by_user",
    "password",
    "refresh_token",
    "serial",
    "ssid",
    "token",
    "url",
    "user_id",
    "username",
    "wifi",
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_MAC_RE = re.compile(r"\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.IGNORECASE)
_OPAQUE_RE = re.compile(r"\b(?:[A-Za-z0-9+/_-]{40,}={0,2})\b")
_SECRET_VALUE_RE = re.compile(
    r"\b(password|token|secret|api[_ -]?key|authorization|ssid|serial)"
    r"\b\s*[:=]\s*[\"']?[^,;\s\"']+",
    re.IGNORECASE,
)

_ANONYMOUS_INSTALLATION_ID_KEY = "anonymous_installation_id"


def _safe_anonymous_installation_id(value: Any) -> str | None:
    """Allow only the deliberately exported canonical UUID v4."""
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        return None
    if (
        parsed.version != 4
        or parsed.variant != RFC_4122
        or str(parsed) != value
    ):
        return None
    return value


def _key_is_sensitive(key: str) -> bool:
    """Return whether a mapping key identifies private installation data."""
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return (
        normalized == "key"
        or normalized.endswith("_key")
        or any(part in normalized for part in _SENSITIVE_KEY_PARTS)
    )


def _sanitize_text(value: str) -> str:
    """Mask common secrets and network/user identifiers in free-form text."""
    sanitized = _SECRET_VALUE_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        value,
    )
    sanitized = _URL_RE.sub("[REDACTED_URL]", sanitized)
    sanitized = _EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)
    sanitized = _IPV4_RE.sub("[REDACTED_IP]", sanitized)
    sanitized = _MAC_RE.sub("[REDACTED_MAC]", sanitized)
    sanitized = _OPAQUE_RE.sub(REDACTED, sanitized)
    if len(sanitized) > MAX_STRING_LENGTH:
        return f"{sanitized[:MAX_STRING_LENGTH]}...[TRUNCATED]"
    return sanitized


def sanitize_diagnostic_value(
    value: Any,
    *,
    key_hint: str = "",
    _depth: int = 0,
) -> Any:
    """Return a JSON-safe value with secrets and personal data masked."""
    if key_hint == _ANONYMOUS_INSTALLATION_ID_KEY:
        return _safe_anonymous_installation_id(value) or REDACTED
    if _key_is_sensitive(key_hint):
        return REDACTED
    if _depth >= MAX_DEPTH:
        return "[MAX_DEPTH_REACHED]"

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                sanitized["_truncated"] = True
                break
            text_key = str(key)
            sanitized[text_key] = sanitize_diagnostic_value(
                item,
                key_hint=text_key,
                _depth=_depth + 1,
            )
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        sanitized_items = [
            sanitize_diagnostic_value(item, _depth=_depth + 1)
            for item in value[:MAX_ITEMS]
        ]
        if len(value) > MAX_ITEMS:
            sanitized_items.append("[TRUNCATED]")
        return sanitized_items

    return _sanitize_text(str(value))

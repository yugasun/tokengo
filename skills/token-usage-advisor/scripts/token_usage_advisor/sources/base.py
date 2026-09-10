"""Helpers common to local source adapters."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from ..models import Evidence


UTC = timezone.utc


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text", "") if isinstance(value.get("text"), str) else ""
    if isinstance(value, list):
        return "\n".join(text(item) for item in value)
    return ""


def identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def redact(value: Any, limit: int = 500) -> str:
    result = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    result = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", result)
    result = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{8,}", r"\1[REDACTED]", result)
    result = re.sub(
        r'''(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*["']?)[^\s,"'}]+''',
        r"\1[REDACTED]",
        result,
    )
    return result[:limit] + ("…" if len(result) > limit else "")


def evidence(path: Any, line: int) -> Evidence:
    path_text = str(path)
    return Evidence(
        source_id=hashlib.sha256(path_text.encode()).hexdigest()[:12],
        line=line,
        path=path_text,
    )

"""Cursor capability-discovery adapter.

Cursor's local stores are versioned blobs without a stable public usage schema.
This adapter intentionally reports activity until a verifiable token field is
found; it never estimates tokens from characters or message counts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import ActivityRecord, Coverage, SourceResult, Usage, UsageRecord
from .base import evidence, identifier, parse_timestamp


class CursorSource:
    name = "cursor"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def _metadata(self) -> list[Path]:
        return sorted(self.root.glob("chats/*/*/meta.json")) + sorted(self.root.glob("chats/*/meta.json"))

    def collect(self) -> SourceResult:
        coverage = Coverage()
        records: list[UsageRecord] = []
        activities: list[ActivityRecord] = []
        paths = self._metadata()
        coverage.files_discovered = len(paths)
        if not self.root.exists():
            coverage.warnings.append("source_unavailable")
            return SourceResult(self.name, "unavailable", coverage=coverage)
        for path in paths:
            try:
                data: Any = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                coverage.malformed_records += 1
                continue
            coverage.files_read += 1
            if not isinstance(data, dict):
                coverage.unsupported_records += 1
                continue
            session_id = identifier(data.get("sessionId") or data.get("session_id") or path.parent.name)
            usage_data = data.get("usage")
            if isinstance(usage_data, dict) and isinstance(usage_data.get("input_tokens"), int):
                records.append(
                    UsageRecord(
                        source=self.name,
                        session_id=session_id,
                        turn_id=None,
                        request_id=identifier(data.get("requestId")),
                        occurred_at=parse_timestamp(data.get("timestamp")),
                        project=identifier(data.get("cwd") or data.get("project")),
                        model=identifier(data.get("model")),
                        effort=identifier(data.get("effort")),
                        usage=Usage.from_mapping(usage_data),
                        quality="experimental",
                        evidence=evidence(path, 1),
                    )
                )
            else:
                activities.append(
                    ActivityRecord(
                        source=self.name,
                        session_id=session_id,
                        kind="session_metadata",
                        occurred_at=parse_timestamp(data.get("timestamp")),
                        evidence=evidence(path, 1),
                    )
                )
        warnings = ["token_fields_unavailable"] if not records else []
        return SourceResult(self.name, "experimental", records, activities, coverage, warnings)

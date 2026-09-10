"""Claude Code transcript adapter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import ActivityRecord, Coverage, SourceResult, Usage, UsageRecord
from .base import evidence, identifier, parse_timestamp, redact, text


class ClaudeSource:
    name = "claude"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def _files(self) -> list[Path]:
        files = set(self.root.glob("projects/**/*.jsonl"))
        files.update(self.root.glob("transcripts/*.jsonl"))
        return sorted(path for path in files if path.is_file())

    @staticmethod
    def _message(row: dict[str, Any]) -> dict[str, Any]:
        message = row.get("message")
        return message if isinstance(message, dict) else {}

    def collect(self) -> SourceResult:
        coverage = Coverage()
        records: list[UsageRecord] = []
        activities: list[ActivityRecord] = []
        seen: set[str] = set()
        files = self._files()
        coverage.files_discovered = len(files)
        if not self.root.exists():
            coverage.warnings.append("source_unavailable")
            return SourceResult(self.name, "unavailable", coverage=coverage)

        for path in files:
            try:
                handle = path.open(encoding="utf-8", errors="replace")
            except OSError:
                coverage.unreadable_files += 1
                continue
            coverage.files_read += 1
            with handle:
                for line_number, raw in enumerate(handle, 1):
                    try:
                        row = json.loads(raw)
                    except (TypeError, ValueError):
                        coverage.malformed_records += 1
                        continue
                    if not isinstance(row, dict):
                        coverage.unsupported_records += 1
                        continue
                    message = self._message(row)
                    role = message.get("role")
                    session_id = identifier(row.get("sessionId") or row.get("session_id"))
                    when = parse_timestamp(row.get("timestamp"))
                    project = identifier(row.get("cwd") or row.get("project"))
                    if role == "assistant" and isinstance(message.get("usage"), dict):
                        usage = Usage.from_mapping(message["usage"])
                        request_id = identifier(
                            message.get("id") or row.get("requestId") or row.get("uuid")
                        )
                        fingerprint = request_id or f"{session_id}|{when}|{usage.to_dict()}"
                        if fingerprint in seen:
                            coverage.duplicate_records += 1
                            continue
                        seen.add(fingerprint)
                        records.append(
                            UsageRecord(
                                source=self.name,
                                session_id=session_id,
                                turn_id=identifier(row.get("turnId") or row.get("turn_id")),
                                request_id=request_id,
                                occurred_at=when,
                                project=project,
                                model=identifier(message.get("model") or row.get("model")),
                                effort=identifier(row.get("effort") or message.get("effort")),
                                usage=usage,
                                quality="exact",
                                evidence=evidence(path, line_number),
                            )
                        )
                        continue

                    kind = None
                    chars = None
                    if role == "user":
                        kind = "user_message"
                        chars = len(text(message.get("content") or row.get("content")))
                    elif role == "assistant":
                        kind = "assistant_activity"
                    elif row.get("type") in {"tool_result", "tool_use"}:
                        kind = str(row["type"])
                    if kind:
                        activities.append(
                            ActivityRecord(
                                source=self.name,
                                session_id=session_id,
                                kind=kind,
                                occurred_at=when,
                                evidence=evidence(path, line_number),
                                chars=chars,
                            )
                        )

        support = "stable" if files else "unavailable"
        warnings = []
        if not records:
            warnings.append("usage_records_not_found")
        return SourceResult(self.name, support, records, activities, coverage, warnings)

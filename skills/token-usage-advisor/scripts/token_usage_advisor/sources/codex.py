"""Codex adapter backed by the tested Codex counter reconciler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import Coverage, SourceResult, Usage, UsageRecord
from .base import evidence


UTC = timezone.utc


def _legacy_module():
    from . import codex_engine
    return codex_engine


class CodexSource:
    name = "codex"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def collect(self, start: datetime | None = None, end: datetime | None = None) -> SourceResult:
        legacy = _legacy_module()
        start = start or datetime(1970, 1, 1, tzinfo=UTC)
        end = end or datetime.now(UTC) + timedelta(days=1)
        if not self.root.exists():
            coverage = Coverage(warnings=["source_unavailable"])
            return SourceResult(self.name, "unavailable", coverage=coverage)
        analyzer = legacy.Analyzer(self.root, start, end).scan()
        coverage = Coverage()
        for key, value in analyzer.coverage.items():
            if key == "malformed_lines":
                coverage.malformed_records = value
            elif key == "files_discovered":
                coverage.files_discovered = value
            elif key == "files_read":
                coverage.files_read = value
            elif key == "unreadable_files":
                coverage.unreadable_files = value
            elif key == "unsupported_records":
                coverage.unsupported_records = value
            elif key == "duplicate_events":
                coverage.duplicate_records = value
            else:
                coverage.warnings.append(f"{key}:{value}")
        records = []
        for row in analyzer.records:
            raw_usage = row.get("usage", {})
            usage = Usage(
                input_tokens=raw_usage.get("input_tokens"),
                cached_input_tokens=raw_usage.get("cached_input_tokens"),
                cache_creation_input_tokens=raw_usage.get("cache_write_input_tokens"),
                output_tokens=raw_usage.get("output_tokens"),
                reasoning_output_tokens=raw_usage.get("reasoning_output_tokens"),
                total_tokens=raw_usage.get("total_tokens"),
            )
            raw_evidence = row.get("evidence", {})
            records.append(
                UsageRecord(
                    source=self.name,
                    session_id=row.get("thread_id"),
                    turn_id=row.get("turn_id"),
                    request_id=row.get("response_id"),
                    occurred_at=row.get("timestamp"),
                    project=row.get("project"),
                    model=row.get("model"),
                    effort=row.get("effort"),
                    usage=usage,
                    quality="exact" if row.get("source") != "unallocated" else "derived",
                    evidence=evidence(raw_evidence.get("path", "codex"), raw_evidence.get("line", 0)),
                )
            )
        warnings = ["unallocated_usage_excluded"] if analyzer.unallocated else []
        return SourceResult(self.name, "stable", records, coverage=coverage, warnings=warnings)

"""Cross-source aggregation; no semantic waste labels live here."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import AnalysisBundle, SourceResult, TOKEN_FIELDS, Usage, UsageRecord


def _sum(records: list[UsageRecord]) -> dict[str, object]:
    values: dict[str, int | None] = {}
    missing: dict[str, int] = {}
    for field in TOKEN_FIELDS:
        known = [getattr(record.usage, field) for record in records if getattr(record.usage, field) is not None]
        values[field] = sum(known) if known else (0 if not records else None)
        if len(known) != len(records):
            missing[field] = len(records) - len(known)
    return {"observed_requests": len(records), "usage": values, "missing_fields": missing}


def _group(records: list[UsageRecord], key: str) -> list[dict[str, object]]:
    grouped: dict[object, list[UsageRecord]] = defaultdict(list)
    for record in records:
        grouped[getattr(record, key)] .append(record)
    result = []
    for value, rows in grouped.items():
        summary = _sum(rows)
        result.append({"key": value, **summary})
    return sorted(result, key=lambda row: row["usage"].get("total_tokens") or 0, reverse=True)


def build_bundle(
    results: Iterable[SourceResult],
    start: datetime,
    end: datetime,
    *,
    project: str | None = None,
    session: str | None = None,
    include_experimental: bool = False,
) -> AnalysisBundle:
    results = list(results)
    precise: list[UsageRecord] = []
    experimental: list[UsageRecord] = []
    source_summaries = []
    evidence_map: dict[str, dict[str, object]] = {}
    for result in results:
        source_summaries.append(
            {
                "name": result.source,
                "support": result.support,
                "records": len(result.records),
                "activities": result.activity_count,
                "warnings": list(result.warnings),
                "coverage": result.coverage.to_dict(),
            }
        )
        for record in result.records:
            if record.occurred_at and not start <= record.occurred_at < end:
                continue
            if project and not (record.project and (record.project == project or record.project.startswith(project.rstrip("/") + "/"))):
                continue
            if session and record.session_id != session:
                continue
            if record.evidence.path:
                evidence_map[record.evidence.source_id] = {"path": record.evidence.path, "line": record.evidence.line}
            if record.quality == "experimental":
                experimental.append(record)
            else:
                precise.append(record)

    by_session: dict[tuple[str, str | None], list[UsageRecord]] = defaultdict(list)
    for record in precise:
        by_session[(record.source, record.session_id)].append(record)
    candidates = []
    for (source, session_id), rows in sorted(by_session.items(), key=lambda pair: sum(r.usage.weight() for r in pair[1]), reverse=True):
        first = rows[0]
        summary = _sum(rows)
        candidates.append(
            {
                "source": source,
                "session_id": session_id,
                "title": session_id or "未知会话",
                "model": first.model,
                "effort": first.effort,
                "total_tokens": summary["usage"].get("total_tokens"),
                "observed_requests": summary["observed_requests"],
                "evidence": [row.evidence.source_id for row in rows[:3]],
            }
        )

    totals = _sum(precise)
    totals["experimental_usage"] = _sum(experimental)
    coverage = {"sources": source_summaries, "precise_records": len(precise), "experimental_records": len(experimental)}
    return AnalysisBundle(
        schema_version="2.0",
        scope={"since": start.isoformat(), "until_exclusive": end.isoformat(), "project": project, "session": session},
        coverage=coverage,
        totals=totals,
        distributions={
            "by_source": _group(precise, "source"),
            "by_model": _group(precise, "model"),
            "by_effort": _group(precise, "effort"),
            "by_project": _group(precise, "project"),
        },
        candidates=candidates[:12],
        sources=evidence_map,
    )

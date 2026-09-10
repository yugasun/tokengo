"""Small, JSON-friendly contracts shared by source adapters and renderers."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


@dataclass
class Usage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def from_mapping(cls, value: Any, *, derive_total: bool = True) -> "Usage":
        value = value if isinstance(value, dict) else {}

        def integer(*names: str) -> int | None:
            for name in names:
                raw = value.get(name)
                if type(raw) is int and raw >= 0:
                    return raw
            return None

        result = cls(
            input_tokens=integer("input_tokens", "prompt_tokens"),
            cached_input_tokens=integer("cached_input_tokens", "cache_read_input_tokens"),
            cache_creation_input_tokens=integer(
                "cache_creation_input_tokens", "cache_write_input_tokens"
            ),
            output_tokens=integer("output_tokens", "completion_tokens"),
            reasoning_output_tokens=integer("reasoning_output_tokens"),
            total_tokens=integer("total_tokens"),
        )
        if result.total_tokens is None and derive_total:
            if result.input_tokens is not None and result.output_tokens is not None:
                result.total_tokens = result.input_tokens + result.output_tokens
        return result

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)

    def weight(self) -> int:
        return self.total_tokens or ((self.input_tokens or 0) + (self.output_tokens or 0))


@dataclass
class Evidence:
    source_id: str
    line: int | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UsageRecord:
    source: str
    session_id: str | None
    turn_id: str | None
    request_id: str | None
    occurred_at: datetime | None
    project: str | None
    model: str | None
    effort: str | None
    usage: Usage
    quality: str
    evidence: Evidence

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["occurred_at"] = self.occurred_at.isoformat() if self.occurred_at else None
        return value


@dataclass
class ActivityRecord:
    source: str
    session_id: str | None
    kind: str
    occurred_at: datetime | None = None
    evidence: Evidence | None = None
    chars: int | None = None


@dataclass
class Coverage:
    files_discovered: int = 0
    files_read: int = 0
    malformed_records: int = 0
    unsupported_records: int = 0
    unreadable_files: int = 0
    duplicate_records: int = 0
    missing_fields: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceResult:
    source: str
    support: str
    records: list[UsageRecord] = field(default_factory=list)
    activities: list[ActivityRecord] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    warnings: list[str] = field(default_factory=list)

    @property
    def activity_count(self) -> int:
        return len(self.activities)

    def __post_init__(self) -> None:
        merged = list(dict.fromkeys(self.warnings + self.coverage.warnings))
        self.warnings = merged


@dataclass
class AnalysisBundle:
    schema_version: str
    scope: dict[str, Any]
    coverage: dict[str, Any]
    totals: dict[str, Any]
    distributions: dict[str, list[dict[str, Any]]]
    candidates: list[dict[str, Any]]
    sources: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    classification: str
    title: str
    fact: str
    hypothesis: str | None
    action: str
    escalation: str | None
    confidence: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class Diagnosis:
    schema_version: str
    conclusion: str
    findings: list[Finding] = field(default_factory=list)
    justified: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    experiments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

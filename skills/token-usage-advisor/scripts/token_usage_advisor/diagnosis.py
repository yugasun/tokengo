"""Validation for the semantic diagnosis document produced by an Agent."""
from __future__ import annotations

from typing import Any


TOP_LEVEL = {"schema_version", "conclusion", "findings", "justified", "rules", "experiments"}
FINDING_FIELDS = {"classification", "title", "fact", "hypothesis", "action", "escalation", "confidence", "evidence"}
CLASSIFICATIONS = {"fact", "hypothesis", "experiment"}
CONFIDENCE = {"low", "medium", "high"}


def validate_diagnosis(value: Any, bundle: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("diagnosis must be an object")
    unknown = set(value) - TOP_LEVEL
    if unknown:
        raise ValueError(f"unknown diagnosis fields: {sorted(unknown)}")
    if value.get("schema_version") != "1.0":
        raise ValueError("unsupported diagnosis schema")
    if not isinstance(value.get("conclusion"), str):
        raise ValueError("diagnosis conclusion must be text")
    for field in ("findings", "justified", "rules", "experiments"):
        if not isinstance(value.get(field), list):
            raise ValueError(f"diagnosis {field} must be a list")
    sources = bundle.get("sources", {}) if isinstance(bundle, dict) else {}
    for index, finding in enumerate(value["findings"]):
        if not isinstance(finding, dict):
            raise ValueError(f"finding {index} must be an object")
        unknown = set(finding) - FINDING_FIELDS
        if unknown:
            raise ValueError(f"unknown finding fields: {sorted(unknown)}")
        if finding.get("classification") not in CLASSIFICATIONS:
            raise ValueError(f"finding {index} has invalid classification")
        if finding.get("confidence") not in CONFIDENCE:
            raise ValueError(f"finding {index} has invalid confidence")
        for required in ("title", "fact", "action"):
            if not isinstance(finding.get(required), str) or not finding[required].strip():
                raise ValueError(f"finding {index} missing {required}")
        refs = finding.get("evidence", [])
        if not isinstance(refs, list) or any(ref not in sources for ref in refs):
            raise ValueError(f"finding {index} references missing evidence")

"""Command-line orchestration for token-usage-advisor."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .aggregate import build_bundle
from .diagnosis import validate_diagnosis
from .html import render_html
from .sources.claude import ClaudeSource
from .sources.codex import CodexSource
from .sources.cursor import CursorSource


UTC = timezone.utc


def _boundary(value: str | None, tz: timezone | ZoneInfo) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)


def _scope(args: argparse.Namespace) -> tuple[datetime, datetime]:
    try:
        tz = ZoneInfo(args.timezone) if args.timezone else datetime.now().astimezone().tzinfo
        end = _boundary(args.until, tz) or datetime.now(tz)
        start = _boundary(args.since, tz) or end - timedelta(days=args.days)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("日期或时区无效；日期使用 ISO 8601，时区使用 IANA 名称")
    if start >= end:
        raise ValueError("since 必须早于 until")
    return start, end


def _sources(args: argparse.Namespace):
    selected = args.source
    if selected == "all":
        selected = ["codex", "claude", "cursor"]
    elif isinstance(selected, str):
        selected = [selected]
    roots = {
        "codex": Path(args.codex_home).expanduser(),
        "claude": Path(args.claude_home).expanduser(),
        "cursor": Path(args.cursor_home).expanduser(),
    }
    adapters = {"codex": CodexSource, "claude": ClaudeSource, "cursor": CursorSource}
    return [adapters[name](roots[name]) for name in selected]


def _write_or_print(payload: str, output: str | None) -> None:
    if output:
        path = Path(output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)


def _collect(args):
    start, end = _scope(args)
    results = []
    for adapter in _sources(args):
        if isinstance(adapter, CodexSource):
            results.append(adapter.collect(start, end))
        else:
            results.append(adapter.collect())
    return build_bundle(
        results,
        start,
        end,
        project=args.project,
        session=getattr(args, "session", None),
        include_experimental=args.include_experimental,
    )


def _probe(adapter) -> dict[str, object]:
    """Cheap health check; never scans transcript contents."""
    if not adapter.root.exists():
        return {"source": adapter.name, "support": "unavailable", "available": False, "files_discovered": 0, "warnings": ["source_unavailable"]}
    if adapter.name == "codex":
        files = set(adapter.root.glob("sessions/**/*.jsonl")) | set(adapter.root.glob("archived_sessions/**/*.jsonl"))
    elif adapter.name == "claude":
        files = set(adapter.root.glob("projects/**/*.jsonl")) | set(adapter.root.glob("transcripts/*.jsonl"))
    else:
        files = set(adapter.root.glob("chats/*/*/meta.json")) | set(adapter.root.glob("chats/*/meta.json"))
    readable = sum(path.is_file() and os.access(path, os.R_OK) for path in files)
    return {
        "source": adapter.name,
        "support": "experimental" if adapter.name == "cursor" else "stable",
        "available": True,
        "files_discovered": len(files),
        "files_readable": readable,
        "warnings": ["token_schema_requires_analyze"] if adapter.name == "cursor" else [],
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="token_usage.py", description="Analyze token usage across local agent applications")
    sub = p.add_subparsers(dest="command", required=True)
    def common(cmd):
        cmd.add_argument("--source", choices=("codex", "claude", "cursor", "all"), default="all")
        cmd.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        cmd.add_argument("--claude-home", default=os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")))
        cmd.add_argument("--cursor-home", default=os.environ.get("CURSOR_HOME", str(Path.home() / ".cursor")))
        cmd.add_argument("--days", type=int, default=7)
        cmd.add_argument("--since")
        cmd.add_argument("--until")
        cmd.add_argument("--timezone")
        cmd.add_argument("--project")
        cmd.add_argument("--include-experimental", action="store_true")
    analyze = sub.add_parser("analyze", help="collect and aggregate usage as JSON")
    common(analyze)
    analyze.add_argument("--output")
    inspect = sub.add_parser("inspect", help="focus analysis on one session")
    common(inspect)
    inspect.add_argument("--session", required=True)
    inspect.add_argument("--output")
    doctor = sub.add_parser("doctor", help="check source availability and fields")
    doctor.add_argument("--source", choices=("codex", "claude", "cursor", "all"), default="all")
    doctor.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    doctor.add_argument("--claude-home", default=os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")))
    doctor.add_argument("--cursor-home", default=os.environ.get("CURSOR_HOME", str(Path.home() / ".cursor")))
    catalog = sub.add_parser("catalog", help="show local source availability")
    catalog.add_argument("--source", choices=("codex", "claude", "cursor", "all"), default="all")
    render = sub.add_parser("render", help="render Agent diagnosis and analysis JSON as HTML")
    render.add_argument("--analysis", required=True)
    render.add_argument("--diagnosis", required=True)
    render.add_argument("--output", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command in {"analyze", "inspect"}:
        if args.days < 1:
            parser().error("days 必须大于 0")
        try:
            bundle = _collect(args)
        except ValueError as exc:
            parser().error(str(exc))
        _write_or_print(json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2) + "\n", args.output)
        return 0
    if args.command == "doctor":
        result = [_probe(adapter) for adapter in _sources(args)]
        sys.stdout.write(json.dumps({"schema_version": "1.0", "sources": result}, ensure_ascii=False, indent=2) + "\n")
        return 0
    if args.command == "catalog":
        result = []
        for adapter in _sources(args):
            result.append({"source": adapter.name, "available": adapter.root.exists(), "root": str(adapter.root)})
        sys.stdout.write(json.dumps({"schema_version": "1.0", "sources": result}, ensure_ascii=False, indent=2) + "\n")
        return 0
    if args.command == "render":
        analysis_path, diagnosis_path = Path(args.analysis).expanduser(), Path(args.diagnosis).expanduser()
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
        validate_diagnosis(diagnosis, analysis)
        html = render_html(analysis, diagnosis)
        _write_or_print(html, args.output)
        return 0
    return 2

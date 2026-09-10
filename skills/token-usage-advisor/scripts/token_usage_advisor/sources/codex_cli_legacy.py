"""Legacy Codex CLI retained only for regression coverage."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .codex_counter import *
from .codex_report import *

def parser():
    p = argparse.ArgumentParser(description="只读分析本机 Codex token 消耗与配置")
    p.add_argument("command", choices=("summary", "inspect"))
    p.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--since")
    p.add_argument("--until", help="不包含此时刻；日期解释为当地零点")
    p.add_argument("--timezone", help="IANA 时区；默认系统本地时区")
    p.add_argument("--project", help="项目目录（包括子目录）或数据库 project_id")
    p.add_argument("--thread", help="完整任务 ID；默认包括可关联子任务")
    p.add_argument("--turn", help="只查看指定轮次")
    p.add_argument("--direct-only", action="store_true")
    p.add_argument("--exclude-thread", action="append", default=[])
    p.add_argument("--include-current", action="store_true")
    p.add_argument("--catalog-only", action="store_true", help="只读取模型目录与默认配置，不扫描会话")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--max-chars", type=int, default=12000)
    p.add_argument("--format", choices=("json", "markdown"), default="markdown",
                   help="summary/inspect 的展示格式；默认 markdown，机器处理请用 json")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.days < 1 or args.offset < 0 or not 1 <= args.limit <= 200 or args.max_chars < 4000:
        p.error("days >= 1，offset >= 0，limit 为 1..200，max-chars >= 4000")
    if args.command == "inspect" and not args.thread:
        p.error("inspect 需要 --thread")
    if args.catalog_only and args.command != "summary":
        p.error("--catalog-only 仅用于 summary")
    if not args.codex_home.is_dir():
        p.error("Codex 数据目录不存在；使用 --codex-home 指定")
    try:
        # astimezone() 对每个无时区日期分别求本地偏移，兼容历史夏令时。
        tz = ZoneInfo(args.timezone) if args.timezone else None
        def boundary(value):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=tz) if dt.tzinfo is None and tz else dt.astimezone(tz)
        end = boundary(args.until) if args.until else datetime.now().astimezone(tz)
        start = boundary(args.since) if args.since else end - timedelta(days=args.days)
        if start >= end:
            p.error("since 必须早于 until")
    except (ValueError, ZoneInfoNotFoundError):
        p.error("日期或时区无效；日期使用 ISO 8601，时区使用 IANA 名称")
    if args.catalog_only:
        report = {"schema_version": VERSION, "command": "catalog", "catalog": read_catalog(args.codex_home)}
    else:
        analyzer = Analyzer(args.codex_home, start, end).scan()
        report = make_report(analyzer, args)
    if args.format == "markdown" and not args.catalog_only:
        sys.stdout.write(render_markdown(report, args.max_chars))
    else:
        sys.stdout.write(bounded_json(report, args.max_chars))


if __name__ == "__main__":
    main()

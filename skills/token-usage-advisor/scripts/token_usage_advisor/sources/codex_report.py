"""Legacy Codex report helpers kept for regression fixtures."""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
import json
from .codex_counter import *

def public_record(r):
    keys = ("thread_id", "turn_id", "model", "effort", "project", "response_id", "source", "usage", "evidence",
            "reason", "may_include_outside_window")
    out = {k: r[k] for k in keys if k in r}
    out["timestamp"] = r["timestamp"].isoformat()
    return out


def distribution(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[r.get(key)].append(r)
    return sorted([{"key": k, **group_stats(v)} for k, v in groups.items()], key=weight, reverse=True)


def task_detail(analyzer, tid, rows, turn_limit=1):
    info = analyzer.metadata.get(tid, {})
    turns = defaultdict(list)
    for r in rows:
        turns[r["turn_id"]].append(r)
    ranked = sorted(turns, key=lambda t: sum(weight(r) for r in turns[t]), reverse=True)
    turn_details = []
    for turn in ranked[:turn_limit]:
        rs = turns[turn]
        f = analyzer.features.get((tid, turn), {})
        detail = {"turn_id": turn, **aggregate(rs),
                  "configurations": sorted({str(r["model"]) + " / " + str(r["effort"]) for r in rs}),
                  "evidence": [rs[0]["evidence"], rs[-1]["evidence"]] if len(rs) > 1 else [rs[0]["evidence"]]}
        for key in ("tool_calls", "tool_output_chars", "largest_tool_output_chars", "error_signals",
                    "compactions", "completion_events", "duration_ms", "instruction_read_signals", "largest_output_evidence"):
            detail[key] = f.get(key)
        detail["repeat_calls"] = sorted([v for v in f.get("calls", {}).values() if v["count"] > 1],
                                        key=lambda x: x["count"], reverse=True)[:3]
        vals = [r["usage"]["input_tokens"] for r in rs if r["usage"]["input_tokens"] is not None]
        detail["per_request_input"] = {"first": vals[0], "last": vals[-1], "max": max(vals)} if vals else None
        turn_details.append(detail)
    # 每个候选任务最多一组短摘录，和明确的轮次及证据绑定。
    sample_turn = ranked[0] if ranked else None
    sample = analyzer.features.get((tid, sample_turn), {})
    return {"thread_id": tid, "title": safe_text(info.get("title") or sample.get("user_excerpt") or tid or "未知任务", 160),
            "parent_thread_id": info.get("parent_thread_id"), "project": info.get("project_id") or info.get("cwd"),
            **aggregate(rows), "turn_count": len(turns), "base_instructions_chars": info.get("base_instructions_chars"),
            "turns": turn_details, "excerpt": {"turn_id": sample_turn,
            "user": safe_text(sample.get("user_excerpt", ""), 500), "result": safe_text(sample.get("result_excerpt", ""), 250),
            "latest_user": sample.get("latest_user_excerpt"),
            "evidence": sample.get("evidence", [])}, "success": "unknown"}


def make_report(analyzer, args):
    rows, unallocated, excluded = analyzer.selected(args)
    groups = defaultdict(list)
    for r in rows:
        groups[r["thread_id"]].append(r)
    ranked = sorted(groups, key=lambda k: sum(weight(r) for r in groups[k]), reverse=True)
    families = defaultdict(list)
    for r in rows:
        root, visited = r["thread_id"], set()
        while root and root not in visited:
            visited.add(root)
            parent = analyzer.metadata.get(root, {}).get("parent_thread_id")
            if not parent:
                break
            root = parent
        if root in visited and analyzer.metadata.get(root, {}).get("parent_thread_id") in visited:
            root = min(str(x) for x in visited)
        families[root].append(r)
    full_catalog = read_catalog(analyzer.home)
    catalog = {k: v for k, v in full_catalog.items() if k != "models"}
    catalog["available_models"] = [x["model"] for x in full_catalog["models"]]
    catalog["details_command"] = "summary --catalog-only"
    report = {"schema_version": VERSION, "command": args.command,
        "scope": {"since": analyzer.start.isoformat(), "until_exclusive": analyzer.end.isoformat(),
                  "thread": args.thread, "turn": args.turn, "project": args.project, "excluded_threads": sorted(x for x in excluded if x),
                  "local_records_only": True},
        "coverage": dict(analyzer.coverage), "catalog": catalog,
        "totals": aggregate(rows), "totals_scope": "unique_observed_requests; unallocated_usage excluded",
        "thread_count": len(groups), "turn_count": len({(r["thread_id"], r["turn_id"]) for r in rows}),
        "by_model": distribution(rows, "model"), "by_effort": distribution(rows, "effort"),
        "by_project": distribution(rows, "project"),
        "by_task": [{"key": k, **group_stats(groups[k])} for k in ranked],
        "task_families": [{"root_thread_id": root, "member_threads": sorted({r["thread_id"] for r in members if r["thread_id"]}),
                           **group_stats(members)} for root, members in families.items()],
        "candidates": [task_detail(analyzer, tid, groups[tid], 3 if args.command == "inspect" else 1) for tid in ranked[:8]],
        "unallocated_usage": {"interval_count": len(unallocated),
                              "known_interval_sums": aggregate(unallocated)["usage"],
                              "missing_fields": aggregate(unallocated)["missing_fields"],
                              "may_overlap_or_cross_window": bool(unallocated),
                              "examples": [public_record(r) for r in unallocated[:3]]},
        "unknown_attribution": aggregate([r for r in rows if not all(r.get(k) for k in ("thread_id", "turn_id", "model", "effort"))]),
        "notes": ["事实统计不等于浪费判断；完成事件不等于成功。", "计数问题覆盖整个扫描；总量仅覆盖筛选窗口。",
                  "缓存输入、推理输出均为子项；缺失字段不是零。", "候选为消耗最高任务，不代表所有语义问题。"]}
    if args.command == "inspect":
        selected = rows[args.offset:args.offset + args.limit]
        report["requests"] = [public_record(r) for r in selected]
        report["pagination"] = {"offset": args.offset, "total_requests": len(rows), "next_offset": None}
    return report


def bounded_json(report, budget):
    """裁剪展示明细而不改统计总量；始终输出有效且有截断说明的 JSON。"""
    report = deepcopy(report)
    report.setdefault("output", {"char_budget": budget, "omitted": {}})
    sources = report.get("sources", {})
    paths = {v: k for k, v in sources.items()}
    def compact(value):
        if isinstance(value, dict):
            if set(value) == {"path", "line"}:
                path = value.pop("path")
                if path not in paths:
                    key = "s" + str(len(paths) + 1)
                    paths[path], sources[key] = key, path
                value["source_id"] = paths[path]
            else:
                for child in value.values():
                    compact(child)
        elif isinstance(value, list):
            for child in value:
                compact(child)
    compact(report)
    def encode():
        used = set()
        def collect(value):
            if isinstance(value, dict):
                if "source_id" in value:
                    used.add(value["source_id"])
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(report)
        report["sources"] = {k: sources[k] for k in sorted(used) if k in sources}
        return json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str)
    def note(key, n=1):
        omitted = report["output"]["omitted"]
        omitted[key] = omitted.get(key, 0) + n
    def size():
        return len(encode()) + 1
    # 优先保持至少一个有上下文的案例，分布和完整请求可另行定向读取。
    while size() > budget:
        if len(report.get("candidates", [])) > 4:
            report["candidates"].pop(); note("candidates"); continue
        if len(report.get("unallocated_usage", {}).get("examples", [])) > 1:
            report["unallocated_usage"]["examples"].pop(); note("unallocated_examples"); continue
        if report.get("requests") and len(report["requests"]) > 1:
            report["requests"].pop(); note("requests"); continue
        trimmed = False
        for k in ("by_task", "task_families", "by_project", "by_model", "by_effort"):
            if len(report.get(k, [])) > 3:
                report[k].pop(); note(k); trimmed = True; break
        if trimmed:
            continue
        if len(report.get("candidates", [])) > 1:
            report["candidates"].pop(); note("candidates"); continue
        candidates = report.get("candidates", [])
        if candidates and len(candidates[0]["turns"]) > 1:
            candidates[0]["turns"].pop(); note("candidate_turns"); continue
        examples = report.get("unallocated_usage", {}).get("examples", [])
        if examples:
            examples.pop(); note("unallocated_examples"); continue
        if report.get("catalog", {}).get("models") and report.get("command") != "catalog":
            report["catalog"]["models"].pop(); note("catalog_models_use_catalog_only"); continue
        if candidates:
            candidates.pop(); note("candidates"); continue
        for k in ("by_task", "task_families", "by_project", "by_model", "by_effort", "requests"):
            if report.get(k):
                report[k].pop(); note(k); trimmed = True; break
        if trimmed:
            continue
        models = report.get("catalog", {}).get("models", [])
        if models:
            models.pop(); note("catalog_models"); continue
        # 超长环境值不能打破字符预算；保留总量和明确说明。
        for k in ("catalog", "notes", "unknown_attribution", "scope"):
            if k in report:
                del report[k]; note(k); trimmed = True; break
        if not trimmed:
            raise ValueError("预算不足以容纳核心统计，请增加 --max-chars")
    if "pagination" in report:
        p = report["pagination"]
        next_offset = p["offset"] + len(report.get("requests", []))
        p["next_offset"] = next_offset if next_offset < p["total_requests"] else None
    rendered = encode() + "\n"
    # pagination 中 null 改为数字可能增加少数字符。
    if len(rendered) > budget and report.get("requests"):
        report["requests"].pop()
        return bounded_json(report, budget)
    return rendered


def fmt_tokens(value):
    if value is None:
        return "未知"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


def pct(part, whole):
    return f"{part / whole * 100:.1f}%" if whole else "未知"


def render_markdown(report, budget=12000):
    """人类阅读版；不重新分析、不添加无证据结论。"""
    total = report.get("totals", {}).get("usage", {})
    request_count = report.get("totals", {}).get("observed_requests", 0) or 0
    total_tokens = total.get("total_tokens") or 0
    scope = report.get("scope", {})
    cov = report.get("coverage", {})
    lines = ["# Codex 使用效率报告", "",
             f"> 分析范围：{scope.get('since', '未知')} 至 {scope.get('until_exclusive', '未知')}（右端不包含）",
             f"> 数据：{report.get('thread_count', 0)} 个任务、{report.get('turn_count', 0)} 个轮次、{request_count:,} 个可确认请求",
             "", "## 先看结论", "",
             f"**可确认消耗：{fmt_tokens(total_tokens)} tokens**。其中输入 {fmt_tokens(total.get('input_tokens'))}，输出 {fmt_tokens(total.get('output_tokens'))}，推理输出 {fmt_tokens(total.get('reasoning_output_tokens'))}。",
             f"缓存输入比例：**{pct(total.get('cached_input_tokens') or 0, total.get('input_tokens') or 0)}**。缓存输入属于输入子项，未重复计入总量。"]
    effort = next((x for x in report.get("by_effort", []) if x.get("key") == "high"), None)
    if effort:
        e = effort.get("usage", {}).get("total_tokens") or 0
        lines.append(f"`high` effort：{effort.get('observed_requests', 0):,} 次，占请求 **{pct(effort.get('observed_requests', 0), request_count)}**，对应 **{pct(e, total_tokens)}** 的可确认 tokens。")
    unallocated = report.get("unallocated_usage", {})
    if unallocated.get("interval_count"):
        lines.append(f"⚠️ 有 **{unallocated['interval_count']} 个累计区间**无法可靠归属（约 {fmt_tokens(unallocated.get('known_interval_sums', {}).get('total_tokens'))}），已从精确总量排除。")
    lines += ["", "## 消耗分布", "", "| 模型 | 请求 | tokens | 占比 |", "|---|---:|---:|---:|"]
    for row in report.get("by_model", [])[:6]:
        amount = row.get("usage", {}).get("total_tokens") or 0
        lines.append(f"| `{row.get('key') or '未知'}` | {row.get('observed_requests', 0):,} | {fmt_tokens(amount)} | {pct(amount, total_tokens)} |")
    lines += ["", "| effort | 请求 | tokens | 占比 |", "|---|---:|---:|---:|"]
    for row in report.get("by_effort", [])[:6]:
        amount = row.get("usage", {}).get("total_tokens") or 0
        lines.append(f"| `{row.get('key') or '未知'}` | {row.get('observed_requests', 0):,} | {fmt_tokens(amount)} | {pct(amount, total_tokens)} |")
    lines += ["", "## 重点任务", ""]
    candidates = report.get("candidates", [])
    if not candidates:
        lines.append("没有足够证据形成重点任务。")
    for index, task in enumerate(candidates[:5], 1):
        amount = task.get("usage", {}).get("total_tokens") or 0
        title = task.get("title", "未知任务").replace("\n", " ")
        lines += [f"### {index}. {title}", "", f"`{task.get('thread_id')}` · {fmt_tokens(amount)} · {task.get('observed_requests', 0):,} 次请求"]
        for turn in task.get("turns", [])[:3]:
            configs = ", ".join(turn.get("configurations", [])) or "配置未知"
            signals = []
            if turn.get("tool_calls") is not None: signals.append(f"工具调用 {turn['tool_calls']}")
            if turn.get("largest_tool_output_chars"): signals.append(f"最大工具输出 {turn['largest_tool_output_chars']:,} 字符")
            if turn.get("error_signals"): signals.append(f"错误信号 {turn['error_signals']}")
            if turn.get("compactions"): signals.append(f"compaction {turn['compactions']} 次")
            if turn.get("repeat_calls"): signals.append("存在重复调用")
            lines.append(f"- 轮次 `{turn.get('turn_id')}`：`{configs}`；{fmt_tokens(turn.get('usage', {}).get('total_tokens'))}。" + ("；".join(signals) + "。" if signals else ""))
        excerpt = task.get("excerpt", {})
        if excerpt.get("user"):
            lines.append(f"- 用户请求：{excerpt['user'].replace(chr(10), ' ')[:240]}")
        if excerpt.get("result"):
            lines.append(f"- 可见结果：{excerpt['result'].replace(chr(10), ' ')[:240]}")
        lines.append("")
    lines += ["## 使用建议", "", "- 明确的局部修改、格式转换和低风险任务，优先从 `low` 开始。", "- 普通多文件开发使用 `medium`；未知根因、高错误代价任务保留 `high`。", "- 出现重复失败时先定位环境或工具原因，再决定是否提高 effort。", "- 工具输出很大时先做摘要或定向提取，减少后续上下文负担。", "", "## 数据完整性", "", f"扫描读取 {cov.get('files_read', cov.get('files_discovered', 0)):,} 个日志文件；事实统计与浪费判断分开。完成事件不代表任务成功。"]
    text = "\n".join(lines) + "\n"
    if len(text) > budget:
        text = text[:max(0, budget - 80)].rstrip() + "\n\n> 报告已按字符预算截断；需要完整任务证据时使用 `inspect --thread <id>`。\n"
    return text



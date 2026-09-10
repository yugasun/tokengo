#!/usr/bin/env python3
"""只读分析 Codex rollout；Python 3.11+，仅标准库。"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
VERSION = "1.0"
FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")
UTC = timezone.utc

def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (AttributeError, TypeError, ValueError):
        return None


def usage(value):
    value = value if isinstance(value, dict) else {}
    return {k: value[k] if type(value.get(k)) is int and value[k] >= 0 else None
            for k in FIELDS}


def signature(value):
    return tuple(value.get(k) for k in FIELDS)


def any_usage(value):
    return any(v is not None for v in value.values())


def subtract(current, previous):
    return {k: current[k] - previous[k]
            if current[k] is not None and previous[k] is not None
            and current[k] >= previous[k] else None for k in FIELDS}


def weight(row):
    u = row.get("usage", row)
    return u.get("total_tokens") or ((u.get("input_tokens") or 0) + (u.get("output_tokens") or 0))


def safe_text(value, limit=800):
    """有限脱敏，不声称是通用秘密检测器。"""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{8,}", r"\1[REDACTED]", value)
    value = re.sub(r'''(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*["']?)[^\s,"'}]+''',
                   r"\1[REDACTED]", value)
    return value[:limit] + ("…" if len(value) > limit else "")


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text", "") if isinstance(value.get("text", ""), str) else ""
    if isinstance(value, list):
        return "\n".join(x.get("text", "") for x in value
                         if isinstance(x, dict) and isinstance(x.get("text", ""), str))
    return ""


def identifier(value):
    return value if isinstance(value, str) and value else None


def evidence(path, line):
    return {"path": str(path), "line": line}


def aggregate(rows):
    rows = list(rows)
    totals, missing = {}, {}
    for k in FIELDS:
        vals = [r["usage"][k] for r in rows if r["usage"].get(k) is not None]
        totals[k] = sum(vals) if vals else (0 if not rows else None)
        if len(vals) != len(rows):
            missing[k] = len(rows) - len(vals)
    # 比例只在所有分子分母均可用且合法时产生。
    inp, cache = totals["input_tokens"], totals["cached_input_tokens"]
    ratio = cache / inp if inp and cache is not None and cache <= inp and not any(
        k in missing for k in ("input_tokens", "cached_input_tokens")) else None
    return {"observed_requests": len(rows), "usage": totals, "missing_fields": missing,
            "cached_input_ratio": round(ratio, 4) if ratio is not None else None}


def group_stats(rows):
    result = aggregate(rows)
    result.pop("cached_input_ratio")
    if not result["missing_fields"]:
        result.pop("missing_fields")
    if result["usage"]["cache_write_input_tokens"] == 0:
        result["usage"].pop("cache_write_input_tokens")
    return result


def read_catalog(home):
    result = {"defaults": {}, "models": [], "warnings": []}
    try:
        cfg = tomllib.loads((home / "config.toml").read_text())
        result["defaults"] = {k: cfg[k] for k in
                              ("model", "model_reasoning_effort", "model_provider", "profile") if k in cfg}
        profile = cfg.get("profiles", {}).get(cfg.get("profile"), {})
        result["active_profile_defaults"] = {k: profile[k] for k in
                                              ("model", "model_reasoning_effort", "model_provider") if k in profile}
    except (OSError, ValueError, TypeError, AttributeError):
        result["warnings"].append("config_unavailable")
    try:
        data = json.loads((home / "models_cache.json").read_text())
        fetched = data.get("fetched_at")
        result["fetched_at"] = fetched
        dt = timestamp(fetched)
        result["cache_age_days"] = round((datetime.now(UTC) - dt).total_seconds() / 86400, 1) if dt else None
        for model in data.get("models", []):
            if not isinstance(model, dict) or not model.get("slug"):
                continue
            result["models"].append({
                "model": model["slug"], "description": safe_text(model.get("description", ""), 180),
                "default_effort": model.get("default_reasoning_level"),
                "supported_efforts": [x.get("effort") for x in model.get("supported_reasoning_levels", [])
                                      if isinstance(x, dict) and x.get("effort")],
            })
    except (OSError, ValueError, TypeError, AttributeError):
        result["warnings"].append("model_catalog_unavailable")
    return result


def read_thread_metadata(home, coverage):
    """数据库只提供元数据；绝不使用 tokens_used/model 补写历史用量或设置。"""
    paths = sorted(home.glob("state_*.sqlite"), key=lambda x: int(x.stem.split("_")[-1])
                   if x.stem.split("_")[-1].isdigit() else -1, reverse=True)
    if not paths:
        coverage["database_unavailable"] += 1
        return {}
    try:
        with closing(sqlite3.connect(paths[0].resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            columns = {r[1] for r in db.execute("pragma table_info(threads)")}
            selected = [k for k in ("id", "title", "cwd", "project_id", "parent_thread_id") if k in columns]
            if "id" not in selected:
                raise sqlite3.DatabaseError("missing id")
            rows = {r["id"]: dict(r) for r in db.execute("select " + ",".join(selected) + " from threads")}
            # schema 探测，未知边字段不猜测。
            edges = {r[1] for r in db.execute("pragma table_info(thread_spawn_edges)")}
            if {"parent_thread_id", "child_thread_id"} <= edges:
                for parent, child in db.execute("select parent_thread_id,child_thread_id from thread_spawn_edges"):
                    rows.setdefault(child, {"id": child})["parent_thread_id"] = parent
            return rows
    except sqlite3.Error:
        coverage["database_unavailable"] += 1
        return {}


class Analyzer:
    def __init__(self, home, start, end):
        self.home, self.start, self.end = Path(home), start, end
        self.coverage = Counter()
        self.metadata = read_thread_metadata(self.home, self.coverage)
        self.streams = []
        self.turn_owners = defaultdict(list)
        self.features = {}
        self.records, self.unallocated = [], []

    def in_window(self, dt):
        return dt is not None and self.start <= dt < self.end

    def feature(self, owner, turn):
        return self.features.setdefault((owner, turn), {
            "thread_id": owner, "turn_id": turn, "user_excerpt": "", "result_excerpt": "",
            "tool_calls": 0, "tool_output_chars": 0, "largest_tool_output_chars": 0,
            "error_signals": 0, "compactions": 0, "completion_events": 0,
            "duration_ms": None, "calls": {}, "evidence": [], "seen": set(),
            "instruction_read_signals": 0,
        })

    def scan(self):
        files = sorted(set(self.home.glob("sessions/**/*.jsonl")) |
                       set(self.home.glob("archived_sessions/**/*.jsonl")))
        self.coverage["files_discovered"] = len(files)
        for path in files:
            self.read_file(path)
        self.normalize()
        return self

    def read_file(self, path):
        owner = None
        turn, model, effort, cwd = None, None, None, None
        observations = []
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            self.coverage["unreadable_files"] += 1
            return
        self.coverage["files_read"] += 1
        with handle:
            for line, raw in enumerate(handle, 1):
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                        self.coverage["unsupported_records"] += 1
                        continue
                except (ValueError, TypeError):
                    self.coverage["malformed_lines"] += 1
                    continue
                kind, p = event.get("type"), event["payload"]
                if not isinstance(kind, str) or (p.get("type") is not None and not isinstance(p["type"], str)):
                    self.coverage["unsupported_records"] += 1
                    continue
                if kind not in ("session_meta", "turn_context", "token_usage_record", "event_msg", "response_item", "compacted"):
                    continue
                dt = timestamp(event.get("timestamp"))
                if kind == "session_meta":
                    owner = identifier(p.get("id"))  # session_id 可以是父任务的，不能作 owner。
                    if not owner:
                        self.coverage["session_meta_without_thread_id"] += 1
                    cwd = identifier(p.get("cwd"))
                    info = self.metadata.setdefault(owner, {})
                    info.setdefault("cwd", cwd)
                    info.setdefault("created_at", identifier(p.get("timestamp")) or identifier(event.get("timestamp")))
                    parent = identifier(p.get("parent_thread_id"))
                    source = p.get("source")
                    if not parent and isinstance(source, dict):
                        subagent = source.get("subagent")
                        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                        parent = identifier(spawn.get("parent_thread_id")) if isinstance(spawn, dict) else None
                    if parent:
                        info["parent_thread_id"] = parent
                    info["base_instructions_chars"] = len(content_text(p.get("base_instructions")))
                    continue
                if kind == "event_msg" and p.get("type") == "task_started":
                    turn = identifier(p.get("turn_id"))
                    model, effort = None, None
                if kind == "turn_context":
                    turn = identifier(p.get("turn_id", turn))
                    model, effort = identifier(p.get("model")), identifier(p.get("effort", p.get("reasoning_effort")))
                    cwd = identifier(p.get("cwd", cwd))
                    if turn and owner:
                        self.turn_owners[turn].append(owner)
                is_new = kind == "token_usage_record"
                is_old = kind == "event_msg" and p.get("type") == "token_count"
                if is_new or is_old:
                    info = p if is_new else (p.get("info") or {})
                    if not isinstance(info, dict):
                        self.coverage["unsupported_usage"] += 1
                        continue
                    u = usage(info.get("usage") if is_new else info.get("last_token_usage"))
                    cumulative = usage(info.get("thread_token_usage") if is_new else info.get("total_token_usage"))
                    if not any_usage(u) and not any_usage(cumulative):
                        continue  # 纯额度快照不是一次请求。
                    obs_turn = identifier(p.get("turn_id", turn)) if is_new else turn
                    observations.append({"thread_id": identifier(p.get("thread_id")) or owner if is_new else owner,
                        "file_owner": owner, "turn_id": obs_turn,
                        "model": model if obs_turn == turn else None,
                        "effort": effort if obs_turn == turn else None,
                        "project": cwd, "timestamp": dt, "usage": u, "cumulative": cumulative,
                        "response_id": identifier(p.get("response_id")) if is_new else None,
                        "source": "request" if is_new else "legacy", "evidence": evidence(path, line)})
                    continue
                if not self.in_window(dt):
                    continue
                f = self.feature(owner, turn)
                subtype = p.get("type")
                ident = (kind, subtype, identifier(p.get("id")) or identifier(p.get("call_id")) or identifier(event.get("timestamp")),
                         hashlib.sha256(raw.encode()).hexdigest() if not p.get("id") and not p.get("call_id") else "")
                if ident in f["seen"]:
                    continue
                f["seen"].add(ident)
                ev = evidence(path, line)
                if kind == "compacted":
                    f["compactions"] += 1
                if kind == "event_msg" and subtype == "task_complete":
                    f["completion_events"] += 1
                    f["duration_ms"] = p.get("duration_ms")
                    f["result_excerpt"] = safe_text(p.get("last_agent_message", ""), 500)
                if kind == "event_msg" and subtype == "user_message":
                    prompt = safe_text(p.get("message", ""), 800)
                    if not f["user_excerpt"]:
                        f["user_excerpt"] = prompt
                    elif prompt != f["user_excerpt"]:
                        f["latest_user_excerpt"] = safe_text(prompt, 200)
                if kind == "response_item" and subtype == "message" and p.get("role") == "user":
                    text = content_text(p.get("content"))
                    if not f["user_excerpt"] and not text.lstrip().startswith(("<environment_context>", "# AGENTS.md", "<permissions", "<recommended_plugins>", "<app-context>")):
                        f["user_excerpt"] = safe_text(text, 800)
                if kind == "response_item" and subtype in ("function_call", "custom_tool_call"):
                    f["tool_calls"] += 1
                    name = identifier(p.get("name")) or "unknown"
                    arg = p.get("arguments", p.get("input", ""))
                    if not isinstance(arg, str):
                        arg = json.dumps(arg, sort_keys=True)
                    key = (name, hashlib.sha256(arg.encode()).hexdigest())
                    call = f["calls"].setdefault(key, {"name": name, "count": 0, "evidence": []})
                    call["count"] += 1
                    if len(call["evidence"]) < 2:
                        call["evidence"].append(ev)
                    if "SKILL.md" in arg or "AGENTS.md" in arg:
                        f["instruction_read_signals"] += 1
                if kind == "response_item" and subtype in ("function_call_output", "custom_tool_call_output"):
                    output = p.get("output", "")
                    if not isinstance(output, str):
                        output = json.dumps(output, ensure_ascii=False)
                    f["tool_output_chars"] += len(output)
                    if len(output) > f["largest_tool_output_chars"]:
                        f["largest_tool_output_chars"] = len(output)
                        f["largest_output_evidence"] = ev
                    if re.search(r'(?i)(Traceback|"isError"\s*:\s*true|exit(?:ed with)?[ _]code["\s:=]+[1-9]|\bError:)', output):
                        f["error_signals"] += 1
                if len(f["evidence"]) < 2 and (f["user_excerpt"] or f["completion_events"]):
                    f["evidence"].append(ev)
        if observations:
            self.streams.append(observations)

    def canonical_owner(self, obs):
        if obs["source"] == "request" and obs["thread_id"]:
            return obs["thread_id"]
        candidates = self.turn_owners.get(obs["turn_id"], [])
        if candidates:
            # 分叉继承 turn_id：优先最早创建的原始任务。
            return min(candidates, key=lambda x: (self.metadata.get(x, {}).get("created_at") or "9999", x))
        return obs["thread_id"]

    def normalize(self):
        requests, legacy = [], []
        for stream in self.streams:
            # 两种累计值在 compaction 后可能永久偏移，绝不能共享差分基线。
            previous = {k: 0 for k in FIELDS}
            previous_time = None
            epoch = 0
            local_seen, snapshots = set(), set()
            new_previous = None
            for obs in stream:
                obs["thread_id"] = self.canonical_owner(obs)
                cumulative, u = obs["cumulative"], obs["usage"]
                dedup = ("response", obs["response_id"]) if obs["response_id"] else (
                    obs["source"], obs["timestamp"], obs["turn_id"], signature(cumulative), signature(u))
                if dedup in local_seen:
                    self.coverage["duplicate_events"] += 1
                    continue
                local_seen.add(dedup)
                if obs["source"] == "request":
                    if new_previous and weight(subtract(cumulative, new_previous)) > weight(u):
                        self.coverage["cumulative_gaps"] += 1
                    if any_usage(cumulative):
                        if new_previous is None and weight(cumulative) > weight(u):
                            self.coverage["initial_cumulative_prefix_unobserved"] += 1
                        new_previous = cumulative
                    requests.append(dict(obs, interval_start=obs["timestamp"]))
                    continue
                has_total = any_usage(cumulative)
                if has_total:
                    reset = any(cumulative[k] is not None and previous[k] is not None
                                and cumulative[k] < previous[k] for k in ("input_tokens", "output_tokens", "total_tokens"))
                    old_key = (obs["thread_id"], obs["turn_id"], signature(cumulative))
                    if old_key in snapshots:
                        self.coverage["duplicate_or_replayed_snapshots"] += 1
                        continue
                    if reset:
                        epoch += 1
                        previous, previous_time = {k: 0 for k in FIELDS}, None
                        self.coverage["counter_resets"] += 1
                    delta = subtract(cumulative, previous)
                    snapshots.add(old_key)
                else:
                    delta = usage(None)
                exact = has_total and all(delta[k] is not None and u[k] is not None and delta[k] == u[k]
                                          for k in ("input_tokens", "output_tokens"))
                resolved = {k: u[k] if exact and u[k] is not None else delta[k] for k in FIELDS}
                record = dict(obs, usage=resolved if has_total else u, last_usage=u,
                              source="legacy_delta" if exact else "unallocated", interval_start=previous_time)
                if not exact:
                    record.update(model=None, effort=None, reason="ambiguous_cumulative_interval" if has_total else "last_usage_without_cumulative")
                record["epoch"] = epoch
                if has_total:
                    previous, previous_time = cumulative, obs["timestamp"]
                if has_total and not any(v for v in delta.values() if v is not None):
                    self.coverage["duplicate_snapshots"] += 1
                    continue
                # 压缩快照中 last_total 可能表示压缩后上下文，而不是输入+输出。
                if u["input_tokens"] == 0 and u["output_tokens"] == 0 and u["total_tokens"]:
                    self.coverage["non_request_compaction_snapshots"] += 1
                    continue
                legacy.append(record)
        # 请求级记录先跨文件去重，再与旧格式逐条对齐。
        seen_responses, seen_legacy = set(), set()
        by_turn = defaultdict(list)
        unique = []
        for r in sorted(requests, key=lambda r: (
                sum(r.get(k) is not None for k in ("thread_id", "turn_id", "model", "effort", "project")),
                r["thread_id"] == r["file_owner"]), reverse=True):
            key = ("response", r["response_id"]) if r["response_id"] else (
                r["thread_id"], r["turn_id"], r["timestamp"], signature(r["usage"]), signature(r["cumulative"]))
            if key in seen_responses:
                self.coverage["inherited_or_duplicate_requests"] += 1
                continue
            seen_responses.add(key)
            r["match_key"] = key
            by_turn[(r["thread_id"], r["turn_id"])].append(r)
            unique.append(r)
        paired = set()
        for r in legacy:
            key = (r["thread_id"], r["turn_id"], r["timestamp"], signature(r["cumulative"]), signature(r["last_usage"]))
            if key in seen_legacy:
                self.coverage["inherited_or_duplicate_requests"] += 1
                continue
            seen_legacy.add(key)
            candidates = by_turn.get((r["thread_id"], r["turn_id"]), [])
            def matches(n):
                if n["match_key"] in paired:
                    return False
                same_total = any_usage(r["cumulative"]) and signature(n["cumulative"]) == signature(r["cumulative"])
                nearby = r["timestamp"] and n["timestamp"] and abs((r["timestamp"] - n["timestamp"]).total_seconds()) <= 30
                same_last = all(r["last_usage"][k] is not None and r["last_usage"][k] == n["usage"][k]
                                for k in ("input_tokens", "output_tokens"))
                return same_total or (nearby and same_last)
            matched = next((n for n in candidates if matches(n)), None)
            if matched:
                paired.add(matched["match_key"])
                self.coverage["overlapping_formats"] += 1
                if r["source"] != "unallocated" and all(r["usage"][k] == matched["usage"][k]
                                                        for k in ("input_tokens", "output_tokens")):
                    continue
                # 区间内已记录的新请求先扣除；余量无法精确归属。
                covered = [n for n in candidates if n["timestamp"] and matched["timestamp"]
                           and n["timestamp"] <= matched["timestamp"]
                           and (r["interval_start"] is None or n["timestamp"] > r["interval_start"])]
                residual = subtract(r["usage"], aggregate(covered)["usage"])
                if not weight(residual):
                    continue
                r.update(usage=residual, source="unallocated", model=None, effort=None,
                         reason="legacy_interval_contains_unobserved_requests")
            elif candidates:
                # 同轮出现新格式时，未对齐旧快照可能是压缩或投影差异，不擅自当成额外请求。
                r.update(source="unallocated", model=None, effort=None, reason="unmatched_legacy_in_request_turn")
                self.coverage["unmatched_legacy_snapshots"] += 1
            unique.append(r)
        for r in unique:
            if r["timestamp"] is None:
                self.coverage["usage_without_timestamp"] += 1
                continue
            if not self.in_window(r["timestamp"]):
                continue
            for a, b in (("cached_input_tokens", "input_tokens"), ("reasoning_output_tokens", "output_tokens")):
                if r["usage"][a] is not None and r["usage"][b] is not None and r["usage"][a] > r["usage"][b]:
                    self.coverage["inconsistent_usage_fields"] += 1
            inp, out, total = (r["usage"][k] for k in ("input_tokens", "output_tokens", "total_tokens"))
            if all(v is not None for v in (inp, out, total)) and total != inp + out:
                self.coverage["usage_total_mismatches"] += 1
            if r["source"] == "unallocated":
                r["may_include_outside_window"] = r["interval_start"] is None or r["interval_start"] < self.start
                self.unallocated.append(r)
            else:
                self.records.append(r)
        self.records.sort(key=lambda r: (r["timestamp"], str(r["thread_id"]), str(r["turn_id"])))
        self.streams.clear()

    def descendants(self, root):
        found = {root}
        while True:
            extra = {k for k, v in self.metadata.items() if v.get("parent_thread_id") in found} - found
            if not extra:
                return found
            found.update(extra)

    def selected(self, args):
        excluded = set(args.exclude_thread)
        current = os.environ.get("CODEX_THREAD_ID")
        if current and not args.include_current:
            excluded.update(self.descendants(current))
        elif not current and not args.include_current:
            self.coverage["current_thread_id_unavailable"] += 1
        wanted = ({args.thread} if args.direct_only else self.descendants(args.thread)) if args.thread else None
        def keep(r):
            tid = r["thread_id"]
            if tid in excluded or (wanted is not None and tid not in wanted):
                return False
            if args.turn and r["turn_id"] != args.turn:
                return False
            if args.project:
                info = self.metadata.get(tid, {})
                cwd = r.get("project") or info.get("cwd")
                project = str(Path(args.project).expanduser().resolve())
                if info.get("project_id") != args.project and not (cwd and (cwd == project or cwd.startswith(project.rstrip("/") + "/"))):
                    return False
            return True
        return [r for r in self.records if keep(r)], [r for r in self.unallocated if keep(r)], excluded

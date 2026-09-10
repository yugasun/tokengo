"""合成日志回归测试；所有文件只写入临时目录。"""
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent))
from token_usage_advisor.sources import codex_engine as m


def u(n, out=10, cached=0, reasoning=3):
    return {"input_tokens": n, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
            "output_tokens": out, "reasoning_output_tokens": reasoning, "total_tokens": n + out}


def add(a, b):
    return {k: a.get(k, 0) + b.get(k, 0) for k in m.FIELDS}


def event(kind, payload, when="2026-09-08T10:00:00Z"):
    return {"timestamp": when, "type": kind, "payload": payload}


def meta(tid="t1", when="2026-09-01T00:00:00Z", **extra):
    return event("session_meta", {"id": tid, "session_id": tid, "timestamp": when, "cwd": "/projects/demo", **extra}, when)


def ctx(turn="v1", model="model-a", effort="high"):
    return event("turn_context", {"turn_id": turn, "model": model, "effort": effort, "cwd": "/projects/demo"})


def new(value, cumulative=None, rid="r1", tid="t1", turn="v1", when="2026-09-08T10:00:01Z"):
    return event("token_usage_record", {"thread_id": tid, "turn_id": turn, "response_id": rid,
                                        "usage": value, "thread_token_usage": cumulative or value}, when)


def old(value, cumulative=None, when="2026-09-08T10:00:02Z"):
    return event("event_msg", {"type": "token_count", "info": {"last_token_usage": value,
                 "total_token_usage": cumulative or value}}, when)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / "sessions").mkdir()
        self.start = datetime(2026, 9, 7, tzinfo=timezone.utc)
        self.end = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows, name="one.jsonl", archive=False):
        folder = self.home / ("archived_sessions" if archive else "sessions")
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def analyze(self):
        return m.Analyzer(self.home, self.start, self.end).scan()

    def args(self, *extra):
        return m.parser().parse_args(["summary", "--codex-home", str(self.home), *extra])

    def test_new_old_and_archive_are_counted_once(self):
        rows = [meta(), ctx(), new(u(100)), old(u(100)), old(u(100))]
        self.write(rows)
        self.write(rows, "copy.jsonl", archive=True)
        a = self.analyze()
        self.assertEqual(len(a.records), 1)
        self.assertEqual(m.aggregate(a.records)["usage"]["total_tokens"], 110)

    def test_legacy_before_new_prefers_request(self):
        self.write([meta(), ctx(), old(u(100)), new(u(100), when="2026-09-08T10:00:03Z")])
        a = self.analyze()
        self.assertEqual(len(a.records), 1)
        self.assertEqual(a.records[0]["source"], "request")

    def test_legacy_deltas_and_reset(self):
        self.write([meta(), ctx(), old(u(100)), old(u(200), u(300, 20, reasoning=6), "2026-09-08T10:01:00Z"),
                    old(u(50), when="2026-09-08T10:02:00Z")])
        a = self.analyze()
        self.assertEqual([r["usage"]["input_tokens"] for r in a.records], [100, 200, 50])
        self.assertEqual(a.coverage["counter_resets"], 1)

    def test_model_switch_same_task_and_unknown_new_turn(self):
        self.write([meta(), ctx(), new(u(100)), ctx("v2", "model-b", "low"),
                    new(u(200), add(u(100), u(200)), "r2", turn="v2", when="2026-09-08T10:01:00Z"),
                    event("event_msg", {"type": "task_started", "turn_id": "v3"}),
                    new(u(300), u(600, 30, reasoning=9), "r3", turn="v3", when="2026-09-08T10:02:00Z")])
        a = self.analyze()
        self.assertEqual([(r["model"], r["effort"]) for r in a.records], [("model-a", "high"), ("model-b", "low"), (None, None)])

    def test_cross_date_resume_uses_event_time_and_baseline(self):
        self.write([meta(when="2026-01-01T00:00:00Z"), ctx(),
                    old(u(100), when="2026-09-06T23:59:00Z"),
                    old(u(200), add(u(100), u(200)))])
        a = self.analyze()
        self.assertEqual(len(a.records), 1)
        self.assertEqual(a.records[0]["usage"]["input_tokens"], 200)

    def test_half_open_time_boundary(self):
        self.write([meta(), ctx(), new(u(100), when=self.start.isoformat()),
                    new(u(200), add(u(100), u(200)), "r2", when=self.end.isoformat())])
        self.assertEqual(len(self.analyze().records), 1)

    def test_fork_inherited_new_records_owner_and_dedup(self):
        original = [meta(), ctx(), new(u(100))]
        self.write(original)
        self.write([meta("fork", "2026-09-08T09:00:00Z", parent_thread_id="t1"), ctx(), new(u(100)),
                    ctx("fork-turn"), new(u(200), add(u(100), u(200)), "r2", tid="fork", turn="fork-turn")], "fork.jsonl")
        a = self.analyze()
        self.assertEqual(sorted((r["thread_id"], r["usage"]["input_tokens"]) for r in a.records), [("fork", 200), ("t1", 100)])

    def test_legacy_fork_and_distinct_subagent_shared_session(self):
        self.write([meta(), ctx(), old(u(100))])
        self.write([meta("fork", "2026-09-08T09:00:00Z"), ctx(), old(u(100)), ctx("fork-turn"),
                    old(u(200), add(u(100), u(200)), "2026-09-08T10:01:00Z")], "fork.jsonl")
        self.write([meta("child", session_id="t1", parent_thread_id="t1"), ctx("child-turn"),
                    new(u(50), tid="child", turn="child-turn", rid="child-r")], "child.jsonl")
        a = self.analyze()
        self.assertEqual(sum(r["usage"]["input_tokens"] for r in a.records), 350)
        self.assertIn("child", a.descendants("t1"))

    def test_missing_fields_remain_unknown_and_partial_sum_marked(self):
        self.write([meta(), ctx(), new({"input_tokens": 100, "output_tokens": 10}),
                    new(u(200), u(300, 20), "r2", when="2026-09-08T10:01:00Z")])
        totals = m.aggregate(self.analyze().records)
        self.assertEqual(totals["missing_fields"]["reasoning_output_tokens"], 1)
        self.assertIsNone(totals["cached_input_ratio"])

    def test_cache_and_reasoning_are_not_added_again(self):
        self.write([meta(), ctx(), new(u(100, 10, 90, 8))])
        totals = m.aggregate(self.analyze().records)
        self.assertEqual(totals["usage"]["total_tokens"], 110)
        self.assertEqual(totals["cached_input_ratio"], .9)

    def test_corrupt_tail_and_unknown_event_do_not_crash(self):
        path = self.write([meta(), ctx(), new(u(100)), event("future_kind", {})])
        with path.open("a") as f:
            f.write('{"type":')
        a = self.analyze()
        self.assertEqual(a.coverage["malformed_lines"], 1)
        self.assertEqual(len(a.records), 1)

    def test_ambiguous_first_cumulative_is_separate(self):
        self.write([meta(), ctx(), old(u(100), u(1000, 100))])
        a = self.analyze()
        self.assertEqual(len(a.records), 0)
        self.assertEqual(a.unallocated[0]["usage"]["input_tokens"], 1000)
        self.assertTrue(a.unallocated[0]["may_include_outside_window"])

    def test_new_replaces_legacy_interval_retaining_residual(self):
        self.write([meta(), ctx(), old(u(100), u(300, 30)), new(u(100), u(300, 30))])
        a = self.analyze()
        self.assertEqual(len(a.records), 1)
        self.assertEqual(a.unallocated[0]["usage"]["input_tokens"], 200)

    def test_old_without_cumulative_not_precise(self):
        self.write([meta(), ctx(), event("event_msg", {"type": "token_count", "info": {"last_token_usage": u(100)}})])
        a = self.analyze()
        self.assertEqual(len(a.records), 0)
        self.assertEqual(len(a.unallocated), 1)

    def test_current_and_children_excluded_but_explicit_override_works(self):
        self.write([meta(), ctx(), new(u(100))])
        self.write([meta("child", parent_thread_id="t1"), ctx("cv"), new(u(50), rid="cr", tid="child", turn="cv")], "child.jsonl")
        a = self.analyze()
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "t1"}):
            self.assertEqual(len(a.selected(self.args())[0]), 0)
            self.assertEqual(len(a.selected(self.args("--include-current"))[0]), 2)

    def test_project_and_thread_filter(self):
        self.write([meta(), ctx(), new(u(100))])
        a = self.analyze()
        self.assertEqual(len(a.selected(self.args("--project", "/projects"))[0]), 1)
        self.assertEqual(len(a.selected(self.args("--project", "/other"))[0]), 0)
        self.assertEqual(len(a.selected(self.args("--thread", "missing"))[0]), 0)

    def test_database_only_supplies_metadata_and_missing_db_is_supported(self):
        self.write([meta(), ctx(), new(u(100))])
        with contextlib.closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("create table threads (id text,title text,cwd text,tokens_used integer,model text)")
            db.execute("insert into threads values ('t1','任务标题','/projects/demo',999999,'wrong-model')")
            db.commit()
        a = self.analyze()
        report = m.make_report(a, self.args())
        self.assertEqual(report["candidates"][0]["title"], "任务标题")
        self.assertEqual(a.records[0]["model"], "model-a")
        self.assertEqual(report["totals"]["usage"]["input_tokens"], 100)

    def test_repeated_tool_and_completion_are_signals_not_success(self):
        rows = [meta(), ctx(), event("response_item", {"type": "message", "role": "user", "content": [{"text": "修正错别字"}]}), new(u(100))]
        for i in range(3):
            rows += [event("response_item", {"type": "function_call", "name": "exec_command", "arguments": '{"cmd":"pytest"}', "call_id": str(i)}),
                     event("response_item", {"type": "function_call_output", "call_id": str(i), "output": "Error: connection refused"})]
        rows.append(event("event_msg", {"type": "task_complete", "last_agent_message": "已结束", "duration_ms": 1000}))
        self.write(rows)
        d = m.make_report(self.analyze(), self.args())["candidates"][0]
        self.assertEqual(d["turns"][0]["repeat_calls"][0]["count"], 3)
        self.assertEqual(d["turns"][0]["error_signals"], 3)
        self.assertEqual(d["success"], "unknown")

    def test_budget_valid_json_and_inspect_pagination(self):
        for i in range(12):
            rows = [meta("t" + str(i)), ctx("v" + str(i)), event("event_msg", {"type": "user_message", "message": "复杂任务" * 1000})]
            total = u(0, 0, reasoning=0)
            for j in range(10):
                total = add(total, u(100))
                rows.append(new(u(100), total, f"r{i}-{j}", f"t{i}", f"v{i}", f"2026-09-08T10:{j:02d}:00Z"))
            self.write(rows, f"{i}.jsonl")
        a = self.analyze()
        report = m.make_report(a, self.args())
        output = m.bounded_json(report, 12000)
        self.assertLessEqual(len(output), 12000)
        data = json.loads(output)
        self.assertEqual(data["totals"]["usage"]["input_tokens"], 12000)
        self.assertLessEqual(len(data["candidates"]), 8)
        args = self.args("--thread", "t0")
        args.command = "inspect"
        data = json.loads(m.bounded_json(m.make_report(a, args), 8000))
        self.assertEqual(data["pagination"]["next_offset"], len(data["requests"]) if len(data["requests"]) < 10 else None)

    def test_catalog_only_does_not_scan_or_read_credentials(self):
        (self.home / "auth.json").write_text("do not read")
        (self.home / "config.toml").write_text('model = "unknown-model"\nmodel_reasoning_effort = "high"\napi_key="sensitive"\n')
        (self.home / "models_cache.json").write_text(json.dumps({"fetched_at": "2026-09-01T00:00:00Z", "models": [{"slug": "unknown-model", "supported_reasoning_levels": [{"effort": "high"}]}]}))
        with patch.object(m.Analyzer, "scan", side_effect=AssertionError("must not scan")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                m.main(["summary", "--codex-home", str(self.home), "--catalog-only"])
        data = json.loads(out.getvalue())
        self.assertEqual(data["catalog"]["models"][0]["supported_efforts"], ["high"])
        self.assertNotIn("sensitive", out.getvalue())

    def test_date_timezone_and_invalid_input(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.main(["summary", "--codex-home", str(self.home), "--since", "2026-09-07", "--until", "2026-09-08", "--timezone", "Asia/Shanghai", "--format", "json"])
        self.assertEqual(json.loads(out.getvalue())["scope"]["since"], "2026-09-07T00:00:00+08:00")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            m.main(["inspect", "--codex-home", str(self.home)])

    def test_redaction_and_no_automatic_waste_label(self):
        text = m.safe_text("api_key=private-value Bearer ABCDEFGHIJKLM sk-1234567890123456")
        self.assertNotIn("private-value", text)
        self.assertNotIn("ABCDEFGHIJKLM", text)
        self.write([meta(), ctx(effort="high"), new(u(100, 10, 95))])
        report = m.make_report(self.analyze(), self.args())
        self.assertNotIn("waste_score", report)
        self.assertEqual(report["candidates"][0]["success"], "unknown")

    def test_compaction_offsets_between_new_and_old_counters(self):
        a, compressed, b = u(100), u(80), u(50)
        rows = [meta(), ctx(), new(a), old(a),
                new(compressed, add(a, compressed), "compact", when="2026-09-08T10:01:00Z"),
                old(u(0, 0), a, "2026-09-08T10:01:01Z"),
                new(b, add(add(a, compressed), b), "r2", when="2026-09-08T10:02:00Z"),
                old(b, add(a, b), "2026-09-08T10:02:01Z")]
        self.write(rows)
        result = self.analyze()
        self.assertEqual(sum(r["usage"]["input_tokens"] for r in result.records), 230)
        self.assertEqual(len(result.unallocated), 0)
        self.assertEqual(result.coverage["counter_resets"], 0)

    def test_distinct_response_ids_with_identical_usage_stay_distinct(self):
        self.write([meta(), ctx(), new(u(100)), old(u(100)),
                    new(u(100), add(u(100), u(100)), "r2", when="2026-09-08T10:00:04Z"),
                    old(u(100), add(u(100), u(100)), "2026-09-08T10:00:05Z")])
        a = self.analyze()
        self.assertEqual(len(a.records), 2)
        self.assertEqual(len(a.unallocated), 0)

    def test_unmatched_mixed_format_is_not_blindly_added(self):
        self.write([meta(), ctx(), new(u(100)), old(u(100)), old(u(200), add(u(100), u(200)), "2026-09-08T10:04:00Z")])
        a = self.analyze()
        self.assertEqual(len(a.records), 1)
        self.assertEqual(a.unallocated[0]["reason"], "unmatched_legacy_in_request_turn")

    def test_parent_family_total_and_source_map(self):
        self.write([meta(), ctx(), new(u(100))])
        self.write([meta("child", parent_thread_id="t1"), ctx("cv"), new(u(50), rid="cr", tid="child", turn="cv")], "child.jsonl")
        report = m.make_report(self.analyze(), self.args())
        self.assertEqual(len(report["task_families"]), 1)
        self.assertEqual(report["task_families"][0]["usage"]["input_tokens"], 150)
        data = json.loads(m.bounded_json(report, 12000))
        ev = data["candidates"][0]["turns"][0]["evidence"][0]
        self.assertTrue(Path(data["sources"][ev["source_id"]]).is_file())

    def test_context_injections_and_dict_base_instructions(self):
        self.write([meta(base_instructions={"text": "指令内容"}, source={"subagent": "review"}), ctx(),
                    event("response_item", {"type": "message", "role": "user", "content": [{"text": "<recommended_plugins>插件列表"}]}),
                    event("event_msg", {"type": "user_message", "message": "调查跨服务并发故障"}),
                    event("event_msg", {"type": "user_message", "message": "继续"}), new(u(100))])
        report = m.make_report(self.analyze(), self.args())
        task = report["candidates"][0]
        self.assertEqual(task["base_instructions_chars"], 4)
        self.assertEqual(task["excerpt"]["user"], "调查跨服务并发故障")
        self.assertEqual(task["excerpt"]["latest_user"], "继续")

    def test_turn_filter_and_nonmutating_renderer(self):
        self.write([meta(), ctx(), new(u(100)), ctx("v2"), new(u(200), u(300, 20), "r2", turn="v2")])
        a = self.analyze()
        args = self.args("--turn", "v2")
        self.assertEqual(len(a.selected(args)[0]), 1)
        m.bounded_json(m.make_report(a, args), 12000)
        self.assertIn("path", a.records[0]["evidence"])

    def test_malformed_identifiers_degrade_to_unknown(self):
        self.write([meta(), event("turn_context", {"turn_id": [], "model": {}, "effort": []}),
                    new(u(100), turn=None), event("response_item", {"type": {"future": True}})])
        a = self.analyze()
        self.assertEqual(a.coverage["unsupported_records"], 1)
        self.assertIsNone(a.records[0]["model"])

    def test_markdown_is_readable_and_uses_total_request_count(self):
        self.write([meta(), ctx(), new(u(100))])
        report = m.make_report(self.analyze(), self.args())
        rendered = m.render_markdown(report, 12000)
        self.assertIn("# Codex 使用效率报告", rendered)
        self.assertIn("1 个可确认请求", rendered)
        self.assertIn("占请求 **100.0%**", rendered)
        self.assertNotIn('"schema_version"', rendered)


if __name__ == "__main__":
    unittest.main()

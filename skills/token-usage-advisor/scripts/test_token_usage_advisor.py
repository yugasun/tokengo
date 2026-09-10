"""Contract tests for the multi-source analyzer and HTML report."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from token_usage_advisor.models import AnalysisBundle, Diagnosis, SourceResult
from token_usage_advisor.aggregate import build_bundle
from token_usage_advisor.diagnosis import validate_diagnosis
from token_usage_advisor.sources.claude import ClaudeSource
from token_usage_advisor.sources.codex import CodexSource
from token_usage_advisor.sources.cursor import CursorSource
from token_usage_advisor.html import render_html


class SourceContractTests(unittest.TestCase):
    def test_bundle_excludes_experimental_tokens_from_precise_total(self):
        from datetime import datetime, timezone
        from token_usage_advisor.models import Evidence, Usage, UsageRecord

        when = datetime(2026, 9, 9, tzinfo=timezone.utc)
        stable = SourceResult(
            "codex",
            "stable",
            [UsageRecord("codex", "s1", "t1", "r1", when, "/p", "m", "low", Usage(input_tokens=10, output_tokens=5, total_tokens=15), "exact", Evidence("e1"))],
        )
        experimental = SourceResult(
            "cursor",
            "experimental",
            [UsageRecord("cursor", "s2", None, "r2", when, "/p", "m", None, Usage(input_tokens=100, output_tokens=5, total_tokens=105), "experimental", Evidence("e2"))],
        )
        bundle = build_bundle([stable, experimental], when.replace(hour=0), when.replace(hour=0) + timedelta(days=1))
        self.assertEqual(bundle.totals["usage"]["total_tokens"], 15)
        self.assertEqual(bundle.totals["experimental_usage"]["usage"]["total_tokens"], 105)

    def test_codex_request_record_is_exposed_through_common_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sessions").mkdir()
            rows = [
                {"timestamp": "2026-09-09T01:00:00Z", "type": "session_meta", "payload": {"id": "codex-1", "cwd": "/projects/demo"}},
                {"timestamp": "2026-09-09T01:00:00Z", "type": "turn_context", "payload": {"turn_id": "turn-1", "model": "codex-test", "effort": "medium", "cwd": "/projects/demo"}},
                {"timestamp": "2026-09-09T01:00:01Z", "type": "token_usage_record", "payload": {"thread_id": "codex-1", "turn_id": "turn-1", "response_id": "response-1", "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}, "thread_token_usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}},
            ]
            path = root / "sessions" / "session.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            result = CodexSource(root).collect()
            self.assertEqual(result.support, "stable")
            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].request_id, "response-1")
            self.assertEqual(result.records[0].usage.total_tokens, 120)

    def test_claude_assistant_usage_is_exact_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "projects" / "demo" / "session.jsonl"
            session.parent.mkdir(parents=True)
            rows = [
                {
                    "type": "user",
                    "sessionId": "claude-1",
                    "cwd": "/projects/demo",
                    "uuid": "u1",
                    "timestamp": "2026-09-09T01:00:00Z",
                    "message": {"role": "user", "content": "修复测试"},
                },
                {
                    "type": "assistant",
                    "sessionId": "claude-1",
                    "cwd": "/projects/demo",
                    "uuid": "a1",
                    "timestamp": "2026-09-09T01:00:01Z",
                    "message": {
                        "id": "msg_1",
                        "model": "claude-test",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "完成"}],
                        "usage": {
                            "input_tokens": 100,
                            "cache_read_input_tokens": 80,
                            "cache_creation_input_tokens": 10,
                            "output_tokens": 25,
                        },
                    },
                },
            ]
            session.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            result = ClaudeSource(root).collect()
            self.assertEqual(result.support, "stable")
            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].usage.input_tokens, 100)
            self.assertEqual(result.records[0].usage.cached_input_tokens, 80)
            self.assertEqual(result.records[0].usage.cache_creation_input_tokens, 10)
            self.assertEqual(result.records[0].quality, "exact")

    def test_cursor_without_usage_is_experimental_activity_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chats" / "chat-1").mkdir(parents=True)
            (root / "chats" / "chat-1" / "meta.json").write_text(
                json.dumps({"sessionId": "cursor-1", "model": "cursor-test"})
            )
            result = CursorSource(root).collect()
            self.assertEqual(result.support, "experimental")
            self.assertEqual(result.records, [])
            self.assertEqual(result.activity_count, 1)
            self.assertIn("token_fields_unavailable", result.warnings)


class HtmlContractTests(unittest.TestCase):
    def test_diagnosis_rejects_unknown_fields_and_missing_evidence(self):
        bundle = {"sources": {"s1": {"path": "/tmp/a", "line": 1}}}
        valid = {
            "schema_version": "1.0",
            "conclusion": "ok",
            "findings": [],
            "justified": [],
            "rules": [],
            "experiments": [],
        }
        validate_diagnosis(valid, bundle)
        with self.assertRaises(ValueError):
            validate_diagnosis({**valid, "surprise": True}, bundle)
        with self.assertRaises(ValueError):
            validate_diagnosis({**valid, "findings": [{"evidence": ["missing"]}]}, bundle)

    def test_html_is_self_contained_and_escapes_diagnosis(self):
        bundle = AnalysisBundle(
            schema_version="2.0",
            scope={"since": "2026-09-08", "until_exclusive": "2026-09-09"},
            coverage={"sources": [{"name": "codex", "support": "stable"}]},
            totals={"observed_requests": 1, "usage": {"total_tokens": 120}},
            distributions={"by_source": [{"key": "codex", "tokens": 120}]},
            candidates=[],
            sources={"s1": {"path": "/tmp/session.jsonl", "line": 3}},
        )
        diagnosis = Diagnosis(
            schema_version="1.0",
            conclusion="<script>alert(1)</script>",
            findings=[],
            justified=[],
            rules=[],
            experiments=[],
        )
        html = render_html(bundle, diagnosis)
        self.assertIn("Token Usage Advisor", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("https://", html)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn('data-filter="claude"', html)


if __name__ == "__main__":
    unittest.main()

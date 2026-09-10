"""Self-contained, escaped HTML report renderer."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from html import escape
import json
from typing import Any

from .models import AnalysisBundle, Diagnosis


def _dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return value if isinstance(value, dict) else {}


def _s(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return escape(str(value), quote=True)


def _num(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return _s(value)


def _tokens(row: dict[str, Any]) -> int:
    usage = row.get("usage", {}) if isinstance(row, dict) else {}
    value = usage.get("total_tokens")
    if isinstance(value, int):
        return value
    return (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)


def _bar_rows(rows: list[dict[str, Any]], total: int) -> str:
    result = []
    for row in rows[:8]:
        amount = row.get("tokens", row.get("total_tokens", _tokens(row)))
        try:
            amount = int(amount or 0)
        except (TypeError, ValueError):
            amount = 0
        width = min(100, round(amount / total * 100, 1)) if total else 0
        result.append(
            '<div class="bar-row"><span class="bar-label">%s</span><span class="bar-track"><i style="width:%s%%"></i></span><strong>%s</strong></div>'
            % (_s(row.get("key")), width, _num(amount))
        )
    return "".join(result) or '<p class="muted">暂无可比较数据</p>'


def render_html(bundle: AnalysisBundle | dict[str, Any], diagnosis: Diagnosis | dict[str, Any]) -> str:
    data = _dict(bundle)
    diag = _dict(diagnosis)
    totals = data.get("totals", {}) if isinstance(data.get("totals"), dict) else {}
    usage = totals.get("usage", {}) if isinstance(totals.get("usage"), dict) else {}
    total_tokens = usage.get("total_tokens") or 0
    coverage = data.get("coverage", {})
    sources = coverage.get("sources", []) if isinstance(coverage, dict) else []
    candidates = data.get("candidates", []) if isinstance(data.get("candidates"), list) else []
    findings = diag.get("findings", []) if isinstance(diag.get("findings"), list) else []
    evidence_map = data.get("sources", {}) if isinstance(data.get("sources"), dict) else {}

    source_badges = "".join(
        '<span class="badge badge-%s">%s · %s</span>'
        % (_s(item.get("support"), "unknown"), _s(item.get("name")), _s(item.get("support")))
        for item in sources
        if isinstance(item, dict)
    ) or '<span class="badge badge-unknown">coverage unavailable</span>'

    task_rows = []
    for index, task in enumerate(candidates[:12], 1):
        if not isinstance(task, dict):
            continue
        task_rows.append(
            '<tr data-source="%s"><td class="rank">%02d</td><td><strong>%s</strong><small>%s</small></td><td>%s</td><td>%s</td></tr>'
            % (
                _s(task.get("source"), "unknown"),
                index,
                _s(task.get("title") or task.get("session_id")),
                _s(task.get("session_id"), ""),
                _num(task.get("total_tokens") or _tokens(task)),
                _s(task.get("effort") or task.get("model")),
            )
        )
    task_table = "".join(task_rows) or '<tr><td colspan="4" class="muted">暂无重点任务</td></tr>'

    finding_cards = []
    for item in findings[:5]:
        if not isinstance(item, dict):
            continue
        refs = item.get("evidence", []) if isinstance(item.get("evidence"), list) else []
        ref_text = ", ".join(_s(evidence_map.get(ref, ref)) for ref in refs[:4]) or "未绑定证据"
        finding_cards.append(
            '<article class="finding"><div class="finding-top"><span class="pill pill-%s">%s</span><span class="confidence">置信度 %s</span></div><h3>%s</h3><p>%s</p><p class="hypothesis"><b>判断：</b>%s</p><div class="action"><b>下一步：</b>%s</div><p class="escalation"><b>升级条件：</b>%s</p><small class="evidence">证据：%s</small></article>'
            % (
                _s(item.get("classification"), "fact"),
                _s(item.get("classification"), "fact"),
                _s(item.get("confidence")),
                _s(item.get("title")),
                _s(item.get("fact")),
                _s(item.get("hypothesis")),
                _s(item.get("action")),
                _s(item.get("escalation")),
                ref_text,
            )
        )
    findings_html = "".join(finding_cards) or '<p class="muted">Agent 尚未形成需要行动的诊断。</p>'

    rules = diag.get("rules", []) if isinstance(diag.get("rules"), list) else []
    experiments = diag.get("experiments", []) if isinstance(diag.get("experiments"), list) else []
    list_html = lambda values: "".join(f"<li>{_s(value)}</li>" for value in values) or '<li class="muted">暂无</li>'
    scope = data.get("scope", {}) if isinstance(data.get("scope"), dict) else {}
    css = """
      :root{--ink:#17211b;--muted:#69756d;--paper:#f6f7f2;--card:#fff;--line:#dfe5dc;--accent:#d4542c;--accent2:#2f6f63;--soft:#edf1e9;--shadow:0 18px 45px rgba(30,47,37,.08)}
      *{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1180px;margin:auto;padding:36px 22px 80px}.masthead{display:flex;justify-content:space-between;gap:24px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:24px}.eyebrow{color:var(--accent);font-size:12px;font-weight:800;letter-spacing:.15em;text-transform:uppercase}.title{font:700 clamp(34px,6vw,70px)/.95 Georgia,"Times New Roman",serif;letter-spacing:-.05em;margin:8px 0 0}.scope{color:var(--muted);max-width:430px;text-align:right}.badges{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 26px}.badge,.pill{display:inline-flex;align-items:center;border-radius:999px;padding:3px 10px;font-size:11px;font-weight:800;letter-spacing:.03em}.badge-stable{background:#dcefe4;color:#1f6b49}.badge-experimental{background:#fff0d1;color:#87500d}.badge-partial,.badge-unknown{background:#f9dfdb;color:#8f3423}.hero{display:grid;grid-template-columns:1.35fr .65fr;gap:18px;margin-bottom:18px}.hero-card,.panel,.finding{background:var(--card);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:18px}.hero-card{padding:28px}.hero-card h2{font:600 26px/1.15 Georgia,serif;margin:0 0 12px}.hero-card p{font-size:18px;max-width:720px}.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.kpi{background:var(--soft);border-radius:14px;padding:16px}.kpi b{display:block;font:700 25px Georgia,serif}.kpi span{color:var(--muted);font-size:12px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;margin:18px 0}.panel{padding:22px}.panel h2{font:600 22px Georgia,serif;margin:0 0 16px}.bar-row{display:grid;grid-template-columns:120px 1fr 72px;gap:10px;align-items:center;margin:12px 0}.bar-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-track{height:9px;background:var(--soft);border-radius:99px;overflow:hidden}.bar-track i{display:block;height:100%;background:linear-gradient(90deg,var(--accent2),#9bc2a9);border-radius:inherit}.filters{display:flex;flex-wrap:wrap;gap:7px;margin:-4px 0 12px}.filters button{border:1px solid var(--line);background:var(--soft);color:var(--ink);border-radius:999px;padding:5px 10px;cursor:pointer;font:inherit;font-size:12px}.filters button:hover,.filters button:focus-visible{border-color:var(--accent);outline:2px solid rgba(212,84,44,.25)}.table-wrap{overflow:auto}.tasks{width:100%;border-collapse:collapse}.tasks th,.tasks td{text-align:left;border-bottom:1px solid var(--line);padding:12px 8px}.tasks th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.tasks small{display:block;color:var(--muted);font-size:11px}.rank{color:var(--accent);font-weight:800}.findings{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.finding{padding:20px;box-shadow:none}.finding-top{display:flex;justify-content:space-between;gap:10px}.pill-fact{background:#e4f1eb;color:#28694d}.pill-hypothesis{background:#fff0d1;color:#87500d}.pill-experiment{background:#e7e5fb;color:#4b4382}.finding h3{font:600 21px/1.15 Georgia,serif;margin:15px 0 8px}.finding p{margin:8px 0}.hypothesis{color:var(--muted)}.action{background:#f5ebe4;border-left:3px solid var(--accent);border-radius:5px;padding:10px 12px;margin-top:13px}.escalation{font-size:13px}.confidence,.evidence,.muted{color:var(--muted)}.evidence{font-size:11px;word-break:break-word}.details{margin-top:18px}.details summary{cursor:pointer;color:var(--accent2);font-weight:700}.details pre{white-space:pre-wrap;font-size:12px;color:var(--muted)}ul{padding-left:20px}.footer{margin-top:34px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:12px}@media(max-width:760px){main{padding:24px 14px 60px}.masthead,.hero{display:block}.scope{text-align:left;margin-top:15px}.grid,.findings{grid-template-columns:1fr}.kpis{margin-top:15px}.bar-row{grid-template-columns:92px 1fr 60px}.title{font-size:48px}}@media(prefers-color-scheme:dark){:root{--ink:#eef3ed;--muted:#a3afa5;--paper:#131916;--card:#1b231e;--line:#2f3d33;--soft:#243128;--shadow:0 18px 45px rgba(0,0,0,.2)}.action{background:#33261f}}@media print{body{background:#fff}.hero-card,.panel,.finding{box-shadow:none}.details{break-inside:avoid}.finding{break-inside:avoid}}
    """
    js = """document.querySelectorAll('[data-filter]').forEach(function(b){b.addEventListener('click',function(){var v=b.dataset.filter;document.querySelectorAll('.tasks tbody tr').forEach(function(r){r.hidden=v!=='all'&&r.dataset.source!==v})})});"""
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';"><title>Token Usage Advisor</title><style>%s</style></head><body><main><header class="masthead"><div><div class="eyebrow">Token Usage Advisor · diagnosis</div><h1 class="title">使用效率<br>诊断报告</h1></div><div class="scope">范围：%s → %s<br><div class="badges">%s</div></div></header><section class="hero"><article class="hero-card"><h2>先看结论</h2><p>%s</p><p class="muted">报告由本地事实统计和 Agent 语义诊断共同生成；缺失字段不会被当作零。</p></article><div class="kpis"><div class="kpi"><b>%s</b><span>可确认 tokens</span></div><div class="kpi"><b>%s</b><span>请求数</span></div><div class="kpi"><b>%s</b><span>输入 tokens</span></div><div class="kpi"><b>%s</b><span>输出 tokens</span></div></div></section><section class="grid"><article class="panel"><h2>按来源</h2>%s</article><article class="panel"><h2>按模型 / effort</h2>%s</article></section><section class="panel"><h2>重点任务</h2><div class="filters"><button type="button" data-filter="all">全部</button><button type="button" data-filter="codex">Codex</button><button type="button" data-filter="claude">Claude Code</button><button type="button" data-filter="cursor">Cursor</button></div><div class="table-wrap"><table class="tasks"><thead><tr><th>#</th><th>任务</th><th>tokens</th><th>配置</th></tr></thead><tbody>%s</tbody></table></div></section><section class="panel" style="margin-top:18px"><h2>Agent 诊断</h2><div class="findings">%s</div></section><section class="grid"><article class="panel"><h2>值得的消耗</h2><ul>%s</ul></article><article class="panel"><h2>个人规则与实验</h2><ul>%s</ul><ul>%s</ul></article></section><details class="details"><summary>查看数据完整性与方法</summary><pre>%s</pre></details><footer class="footer">本报告离线生成，不含远程资源、分析脚本或完整原始日志。HTML schema：1.0。</footer></main><script>%s</script></body></html>""" % (
        css,
        _s(scope.get("since")),
        _s(scope.get("until_exclusive") or scope.get("until")),
        source_badges,
        _s(diag.get("conclusion"), "暂无结论"),
        _num(total_tokens),
        _num(totals.get("observed_requests")),
        _num(usage.get("input_tokens")),
        _num(usage.get("output_tokens")),
        _bar_rows(data.get("distributions", {}).get("by_source", []), total_tokens),
        _bar_rows(data.get("distributions", {}).get("by_model", []) or data.get("distributions", {}).get("by_effort", []), total_tokens),
        task_table,
        findings_html,
        list_html(diag.get("justified", [])),
        list_html(rules),
        list_html(experiments),
        _s(json.dumps(coverage, ensure_ascii=False, indent=2), "暂无"),
        js,
    )

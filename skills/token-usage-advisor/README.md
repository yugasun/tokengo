# Token Usage Advisor

Token Usage Advisor is a local, read-only skill for understanding how the
invoking Agent platform consumes context and tokens. It supports Codex and
Claude Code, with experimental Cursor activity support. It combines
deterministic accounting with the invoking Agent's semantic reasoning and
produces a polished, offline HTML diagnosis report.

## What it answers

- Which sessions and task shapes account for observed usage?
- Is a high model/effort choice supported by task difficulty and quality?
- Are repeated tools, oversized context, or rework adding cost?
- What one configuration should be tried next, and when should it escalate?

The tool reports tokens involved, not tokens promised as saveable. Missing data
is visible as missing. Cursor remains experimental and never gets token estimates
from character counts.

## Run locally

```bash
# Replace codex with the invoking platform: codex, claude, or cursor.
python3 scripts/token_usage.py doctor --source codex
python3 scripts/token_usage.py analyze --source codex --output /tmp/usage-analysis.json

# Explicit cross-platform comparison only when requested.
python3 scripts/token_usage.py analyze --source all --output /tmp/usage-analysis.json
```

The skill's default scope is all tasks on the invoking platform for the
previous seven local calendar days. It does not automatically narrow to the
current project or session. Use `--project` or `--session` for an explicitly
focused analysis. Cursor requires `--include-experimental` and never gets
precise token estimates from activity or character counts.

An Agent turns the evidence bundle into `diagnosis.json`, then renders the
self-contained report:

```bash
python3 scripts/token_usage.py render \
  --analysis /tmp/usage-analysis.json \
  --diagnosis /tmp/diagnosis.json \
  --output /tmp/token-usage-report.html
```

The CLI uses only Python 3.11+ standard-library modules and does not access the
network or credentials. Local paths can be overridden with `--codex-home`,
`--claude-home`, and `--cursor-home`.

## Skill installation

This skill ships in the [tokengo](https://github.com/yugasun/tokengo)
collection. The usual install is:

```bash
npx skills add yugasun/tokengo --skill token-usage-advisor -g
```

You can also copy or symlink this directory into
`~/.codex/skills/token-usage-advisor/` (or the configured skills directory).
Invoke it with `$token-usage-advisor` for a retrospective, a focused
inspection, or a prospective model/effort recommendation.

## Development

```bash
PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test*.py'
python3 scripts/token_usage.py --help
```

All fixtures are synthetic. Do not commit local session logs, generated reports,
account identifiers, or credentials.

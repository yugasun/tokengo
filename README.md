# tokengo

[中文文档](README.zh-CN.md)

tokengo is a collection of local, read-only [Agent Skills](https://agentskills.io) for understanding how AI coding agents spend context and tokens. Skills live in `skills/` and install with the standard skills CLI.

The first skill, [token-usage-advisor](skills/token-usage-advisor), analyzes Codex and Claude Code usage, with experimental Cursor activity support. It combines deterministic accounting with the invoking agent's semantic reasoning and produces a polished, offline HTML diagnosis report.

## Install

```bash
npx skills add yugasun/tokengo -g
```

The `-g` flag installs into your user skill directories so the skill is available across projects. Omit it to install into the current project instead.

To install only one skill:

```bash
npx skills add yugasun/tokengo --skill token-usage-advisor -g
```

You can also copy a skill directory by hand, for example:

```bash
git clone git@github.com:yugasun/tokengo.git
ln -s "$(pwd)/tokengo/skills/token-usage-advisor" ~/.codex/skills/token-usage-advisor
```

## Skills

| Skill | What it does |
| --- | --- |
| [`token-usage-advisor`](skills/token-usage-advisor) | Diagnose token usage on the invoking agent platform and render an offline HTML report |

`token-usage-advisor` answers:

- Which sessions and task shapes account for observed usage?
- Is a high model/effort choice supported by task difficulty and quality?
- Are repeated tools, oversized context, or rework adding cost?
- What one configuration should be tried next, and when should it escalate?

It reports tokens involved, not tokens promised as saveable. Missing data is visible as missing. Cursor remains experimental and never gets token estimates from character counts.

## Development

Each skill is self-contained. From `skills/token-usage-advisor`:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test*.py'
python3 scripts/token_usage.py --help
```

The CLI uses only Python 3.11+ standard-library modules. It does not access the network or credentials. Do not commit local session logs, generated reports, account identifiers, or credentials.

See [CONTRIBUTING.md](CONTRIBUTING.md) if you want to change an existing skill or add another one under `skills/`.

## License

[MIT](LICENSE)

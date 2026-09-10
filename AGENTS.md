# tokengo

This repository is a collection of Agent Skills. Each skill lives in `skills/<name>/` and must contain `SKILL.md`. The directory name and the frontmatter `name` must match.

Keep CLIs deterministic and local. The invoking agent owns semantic diagnosis. Do not add network access, credential reads, or writes to agent logs.

## token-usage-advisor

From `skills/token-usage-advisor`:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test*.py'
python3 scripts/token_usage.py --help
```

Read `skills/token-usage-advisor/SKILL.md` before changing analysis scope, coverage rules, or the diagnosis contract.

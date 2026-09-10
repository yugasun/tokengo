# Contributing

tokengo is a collection of Agent Skills. Each skill is a directory under `skills/` with a `SKILL.md` and, when needed, `scripts/`, `references/`, and `examples/`.

## Change an existing skill

1. Keep collection, aggregation, and rendering deterministic. Leave semantic diagnosis to the invoking agent.
2. Stay local and read-only: no network, no credentials, no writes to agent logs or settings.
3. Add or update tests next to the skill scripts.
4. Run the skill tests before opening a pull request.

For `token-usage-advisor`:

```bash
cd skills/token-usage-advisor
PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test*.py'
python3 scripts/token_usage.py --help
```

All fixtures must be synthetic.

## Add a skill

1. Create `skills/<skill-name>/SKILL.md`. The `name` field must match the directory name.
2. Bundle scripts and references inside that directory so the skill remains installable on its own.
3. Link the new skill from the root README.
4. If the skill has tests, add a CI job or extend the existing workflow with a `working-directory` for that skill.

## Pull requests

Use [Angular commit messages](https://github.com/conventional-changelog/conventional-changelog/blob/master/packages/conventional-changelog-angular/README.md), for example `feat(token-usage-advisor): ...`. Describe why the change exists; do not list files.

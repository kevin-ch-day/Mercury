# Contributing to Mercury

Thank you for contributing. Mercury protects production MariaDB databases on a security research platform — **safety rules are non-negotiable**.

## Before you start

1. Read [AGENTS.md](AGENTS.md) — safety policy, layout, and agent workflow.
2. For implementation recipes, see [docs/ai_extension_points.md](docs/ai_extension_points.md).

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[mariadb,dev]"
mercury config init   # creates gitignored local config
python -m pytest
```

Do **not** commit `config/local.toml`, `config/databases.toml`, or credentials.

## Pull requests

- Keep diffs focused; match existing style.
- Add or update tests for behavior changes.
- Ensure `python -m pytest` passes locally.
- Do not weaken production protection (`*_prod`, dry-run gates, read-only live SQL).
- Update docs when changing CLI commands or policy behavior.

## Safety reminders

- Backup sources: `*_prod` and `android_permission_intel` only.
- Never back up `*_dev` by default; never restore into `*_prod`.
- Live writes require explicit config gates (`dry_run=false` and `live_actions_enabled=true`).

## Questions

Open an issue on GitHub for bugs, feature requests, or policy questions.

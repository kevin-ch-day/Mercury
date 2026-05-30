# Mercury — Copilot / Codex instructions

This repo is a **Fedora-first Python CLI** for MariaDB backup, DR, and prod→dev sync on a security research platform.

## Required reading

1. **[AGENTS.md](../AGENTS.md)** — safety policy, layout, CLI, pitfalls (authoritative)
2. **[docs/ai_extension_points.md](../docs/ai_extension_points.md)** — recipes for CLI, backup, DB, tests

## Safety (never violate)

- Backup only `*_prod` and `android_permission_intel`. Never `*_dev`.
- Never drop/overwrite/restore into `*_prod`.
- Dry-run default; live writes need explicit config gates.
- Live SQL: read-only only.

## Stack

Python 3.12+, Typer, Rich, Pydantic, pymysql (optional). Entry: `mercury.cli:main`.

## Before submitting changes

```bash
pip install -e ".[mariadb,dev]"
python -m pytest
```

Use `.venv/bin/python`. Do not commit `config/local.toml`.

## Code location

- Logic: `src/mercury/{core,backup,database,config,env,reporting,sync}/`
- CLI: `src/mercury/cli.py`, `src/mercury/database/cli.py`
- Tests: `tests/test_*.py`
- Top-level `src/mercury/*.py` shims (except cli/menu) — do not extend

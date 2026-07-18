# Mercury — ChatGPT / Codex / Copilot instructions

This repo is a **Fedora- and Windows-supported Python CLI** for MariaDB backup, DR, and prod→dev sync on a security research platform.

## Required reading

1. **[AGENTS.md](../AGENTS.md)** — safety policy, layout, CLI, pitfalls (authoritative)
   Compatibility note: some tools may discover the lowercase root file [agents.md](../agents.md), which points back to `AGENTS.md`.
2. **[docs/ai_extension_points.md](../docs/ai_extension_points.md)** — recipes for CLI, backup, DB, tests

## Safety (never violate)

- Backup only `*_prod` and `android_permission_intel`. Never `*_dev`.
- Never drop/overwrite/restore into `*_prod`.
- **Backup writes** require a safe operator-storage environment (legacy USB until cutover); **sync/deploy/restore** additionally require explicit live-action gates.
- Live execution is supported on **Fedora and Windows** when configured; non-Fedora Linux is seed/status only.
- Live backup execution requires the USB layout under `[mercury].backup_root` (Linux default `/mnt/MERCURY_DATA_USB/mercury_backups`); repo-local `backups/` is dev-only.
- When USB is plugged in but unmounted, doctor repair plan suggests `sudo systemctl start mnt-MERCURY_DATA_USB.mount` (fstab) or mount-by-label.
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
- CLI: `src/mercury/cli.py`, `src/mercury/database/commands.py`
- Menu / terminal: `src/mercury/menu/`, `src/mercury/terminal/`
- Tests: `tests/test_*.py`
- Top-level `src/mercury/*.py` shims (except cli/menu) — do not extend

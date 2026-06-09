# AGENTS.md — Mercury

Guidance for AI coding agents (Cursor, ChatGPT, Codex) working in this repository.

## Start here (agents)

1. Read **Non-negotiable safety policy** below before any code change.
2. Use **[docs/ai_extension_points.md](docs/ai_extension_points.md)** for recipes (add CLI, backup step, DB feature, tests).
3. **Cursor:** project rules live in [.cursor/rules/](.cursor/rules/) (`mercury-safety.mdc` always applies).
4. **ChatGPT / Codex:** [.github/copilot-instructions.md](.github/copilot-instructions.md) points here.
5. Run tests before finishing: `.venv/bin/python -m pytest`

| Task | Start in |
|------|----------|
| New backup feature | `backup/backup_runner.py`, `core/execution_policy.py`, `tests/test_backup_execute.py` |
| New DB command | `database/commands.py`, `database/mariadb/`, matching `tests/test_m*.py` |
| New CLI (non-db) | `cli.py`, `menu/runners.py`, `tests/test_cli_*.py` |
| Policy/report | `reporting/protection.py`, `core/safety.py` |
| Classification | `database/core/catalog.py`, `database/core/classifier.py` |

**Imports:** prefer subpackages (`mercury.backup.backup_runner`, `mercury.core.safety`, `mercury.logging`). Top-level shims (`mercury.safety`, `mercury.verification`) remain for external compatibility only — do not use them in new `src/` or test code.

## What Mercury is

Mercury is a **Fedora-first Python CLI** for MariaDB backup, disaster recovery, schema export, verification, prod→dev sync planning, Git repository transfer bundles, and transfer manifests/runbooks on an Android security research platform.

It is **not** an AI tool, web app, malware analyzer, or workstation/bootstrap utility.

**Production target:** Fedora for live operations. Windows and non-Fedora Linux are acceptable for seed planning/development only.

## Non-negotiable safety policy

Preserve these rules in every change. Do not weaken them.

1. **Protect production first** — backup sources are `*_prod` and `android_permission_intel` only.
2. **Never back up `*_dev` by default** — dev DBs are disposable sync targets.
3. **Never drop, overwrite, or restore into `*_prod`** by default.
4. **Always backup and verify prod** before any prod→dev sync.
5. **Require typing `SYNC DEV`** before any future dev sync execution.
6. **`_restorecheck_*` databases** are never backup sources.
7. **Unknown-role databases** require manual review before action.
8. A database is **not protected** until verification passes (manifest + checksum + size).
9. **Dry-run is the default** — live writes (`mariadb-dump`, file artifacts, sync) require explicit policy gates.

Policy constants live in `src/mercury/core/safety.py`. Execution gates live in `src/mercury/core/execution_policy.py`.

## Current development phase

**Seed / dry-run** is still the default runtime mode:

- Planning, discovery, manifests, and reports are implemented.
- Live **read-only** server access works (`db ping`, `db discover`, `db inspect`, `db access`).
- Backup **execution** exists but is gated (`backup run --execute` requires `dry_run=false` and `live_actions_enabled=true`).
- Prod→dev **sync execution** and restore-check execution exist but are gated the same way (`sync run --execute`, `restore-check run --execute`).
- Menu and CLI default to dry-run; live writes require explicit policy in `config/local.toml` or env vars.
- Live execution is Fedora-only; unsupported hosts remain seed/status only even when dry-run is disabled.

## Repository layout

```
src/mercury/
  cli.py                   # Typer entrypoint (`mercury` command)

  menu/                    # Interactive menu loop, prompts, dashboard, runners
    loop.py, runners.py, main_display.py, prompts.py, …
  terminal/                # Shared CLI formatting (format, screen, table)
  menu.py, menu_*.py       # Thin shims → menu.* (backward compat)
  display_*.py, terminal_*.py  # Thin shims → terminal.* (backward compat)
  paths.py, safety.py, …   # Thin shims → core.* (backward compat)

  core/                    # Paths, policy, runtime, output, execution gates
  backup/                  # backup_runner.py, batch_runner.py, terminal/, …
  config/
  env/                     # terminal/, interactive_menu.py, probe.py
  logging/                 # terminal/, engine.py, events.py, analysis.py
  reporting/               # terminal/, protection.py, preview.py
  repo/                    # configured Git repo status, bundle, manifest, runbook
  restore/                 # check_plan.py, restore_runner.py, terminal/, …
  sync/                    # sync_plan.py, sync_runner.py, terminal/, …
  transfer/                # aggregate database+repo transfer manifest and runbook
  database/
    core/                  # Models, catalog, classifier, inventory
    terminal/              # CLI output (inventory, inspect, ping, policy, …)
    facade.py              # DatabaseService entry point
    commands.py            # `mercury db` / `mercury database` Typer commands
    discovery/, mariadb/, prod_dev_pairs.py, backup_planning.py, …
```

**Naming:** shared terminal helpers live in `mercury.terminal`; domain CLI output lives in `<package>/terminal/` (or legacy `*_terminal.py` shims); execution uses `*_runner.py`; feature menus use `interactive_menu.py`. Prefer canonical import paths (`mercury.backup.terminal.verify`, `mercury.database.terminal.inventory`). Top-level shims remain for compatibility.

Policy constants: `src/mercury/core/safety.py`. Execution gates: `src/mercury/core/execution_policy.py`.

**Terminal theme:** Mercury uses a restrained semantic terminal theme via `mercury.terminal.theme` and Rich-backed `mercury.core.output`. The main menu is a plain operator console with a compact status summary and workflow-focused action list. Colors apply on TTY stdout only. Disable with `NO_COLOR` or `MERCURY_NO_COLOR=1`; force with `MERCURY_FORCE_COLOR=1` (overrides `NO_COLOR`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[mariadb,dev]"
```

Run tests before finishing work:

```bash
python -m pytest
```

Use the project venv (`.venv/bin/python`), not system Python, when validating CLI behavior.

**Startup:** `./run.sh` skips `pip install` when `.venv/.mercury-sync-stamp` is newer than `pyproject.toml` and `src/` (set `MERCURY_SKIP_SYNC=1` to skip always). Database Typer commands live in `mercury.db_commands` (outside the heavy `mercury.database` package). `main()` calls `prepare_for_argv()` so `db`/`database` subcommands are wired only when argv needs them (~80ms import for `mercury menu`; full database stack loads on first command that uses it).

## Platform databases

| Project | Databases |
|---------|-----------|
| Erebus | `erebus_threat_intel_prod` / `_dev` |
| Platform | `android_permission_intel` (shared authority) |
| ScytaleDroid | `scytaledroid_core_prod` / `_dev` |

For the current Fedora milestone, Mercury actively protects only:

- `android_permission_intel`
- `erebus_threat_intel_prod`
- `scytaledroid_core_prod`

and plans prod→dev sync readiness only for:

- `erebus_threat_intel_prod` -> `erebus_threat_intel_dev`
- `scytaledroid_core_prod` -> `scytaledroid_core_dev`

Out-of-scope databases such as `gecko_research_database_*`, `droid_threat_intel_db_prod`, and `proofpoint_cti_db_dev` may appear in live discovery for operator awareness, but they are excluded from backup/sync planning and do not count as blockers for this milestone.

Catalog reference: `src/mercury/database/core/catalog.py`  
Classification: `src/mercury/database/core/classifier.py`

## Configuration

```bash
mercury config init   # copies example → config/local.toml, config/databases.toml, config/repos.toml
mercury repo init-config   # writes config/repos.toml from known Fedora desktop repo paths
```

**Live read-only access (local Fedora socket example):**

```toml
# config/local.toml
[mariadb]
host = "127.0.0.1"
port = 3306
user = "root"
use_client = true
unix_socket = "/var/lib/mysql/mysql.sock"
```

**Remote / password auth:**

```toml
user = "mercury_readonly"
password_env = "MERCURY_MARIADB_PASSWORD"
use_client = false
```

Then: `export MERCURY_MARIADB_PASSWORD=...`

**Live backup execution (explicit only):**

```toml
[mercury]
dry_run = false
live_actions_enabled = true
backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"
```

For the first live milestone, Mercury also requires the USB mount under `/mnt/MERCURY_DATA_USB` to be active. Repo-local `backups/` are development artifacts only and do not count as production protection in live/operator mode.

Never commit passwords or `config/local.toml`.

## MariaDB access modes

Mercury supports two read-only connection paths:

| Mode | When | Module |
|------|------|--------|
| `use_client = true` | Fedora local socket auth via `mariadb`/`mysql` CLI | `database/mariadb/client.py` |
| `use_client = false` | TCP + pymysql | `database/mariadb/session.py` |

Unified helpers: `readonly_scalar()`, `readonly_scalars()`, `fetch_user_database_names()`, `probe_mariadb_server()`.

All live SQL must remain **read-only** (`SELECT`, `SHOW DATABASES`, `information_schema` queries). No DDL/DML.

## Key CLI commands

```bash
mercury db ping                          # read-only connectivity probe
mercury db discover                      # live SHOW DATABASES + classify
mercury db discover --demo               # offline catalog/config
mercury db inspect --name <db>           # tables/views/size (read-only)
mercury db access                        # catalog vs server presence
mercury status                           # protection report (demo inventory)
mercury status --live                    # protection report from live server
mercury backup run --db <prod> --kind full          # dry-run plan (default)
mercury backup run --db <prod> --kind full --execute  # gated live dump
mercury backup verify --db <prod> [--update-manifest]  # verify on-disk artifacts
mercury menu
```

## Code conventions

- **Python 3.12+**, type hints, Pydantic models for structured data.
- **Typer** for CLI, **Rich**-backed output via `mercury.output`.
- **Small, tested diffs** — match existing module style; do not over-abstract.
- **Reuse** `classify_database()`, `build_backup_layout()`, `execution_policy`, and existing display helpers.
- **Tests** belong in `tests/test_*.py`; use mocks/`tmp_path` for file IO; use `probe_fn` / `connect_fn` / `dump_runner` injection for DB/subprocess fakes.
- **Integration tests** against local MariaDB may run when `/var/lib/mysql/mysql.sock` exists — guard with `pytest.skip` or `@pytest.mark.skipif`.
- Do **not** add unrelated refactors, unsolicited README/doc updates, or commits unless asked.

## Backup artifact layout

```
backups/YYYY-MM-DD/<database>/
  <database>_<timestamp>.sql.gz
  <database>_<timestamp>.schema.sql.gz
  manifest.json
  checksum.sha256
  backup_report.md
```

Manifest builder: `build_backup_manifest()` in `backup/manifest.py`.  
Checksum helpers: `backup/checksum.py`.  
Verification: `verify_backup_artifacts()` in `backup/verification.py`.

Restore-check behavior: successful restore-check imports auto-drop the `_restorecheck_*` database; failed runs preserve it for debugging and print the cleanup command.

## Suggested agent workflow

1. Read relevant modules and matching tests before editing behavior.
2. Check `.cursor/rules/` if using Cursor (safety rule always applies).
3. Preserve all existing tests; add focused tests for new behavior.
4. Run `python -m pytest` from repo root with `.venv`.
5. For live DB features, verify read-only constraints and policy gates.
6. Never suggest or implement commands that drop/overwrite `*_prod` or restore into prod.
7. Never assume credentials exist — handle missing config with clear errors pointing to `mercury config init`.
8. Do not commit unless explicitly asked. Never commit `config/local.toml`.

## Test patterns (quick reference)

| Inject / mock | Use for |
|---------------|---------|
| `execute_backup(..., dump_runner=fake)` | Backup without real `mariadb-dump` |
| `probe_mariadb_server(..., probe_fn=fake)` | Connectivity without socket |
| `ExecutionPolicy(...)` / `local_config=` | Dry-run vs live gates |
| `monkeypatch.setattr("mercury.paths.OUTPUT_DIR", tmp_path)` | File output in tests |
| `subprocess.run([sys.executable, "-m", "mercury.cli", ...])` | CLI integration (separate process) |

Full test file index: [docs/ai_extension_points.md](docs/ai_extension_points.md#test-file-index).

## Documentation map

- [README.md](README.md) — operator quick start
- [docs/ai_extension_points.md](docs/ai_extension_points.md) — **agent cookbook** (CLI, backup, DB, tests)
- [.cursor/rules/](.cursor/rules/) — Cursor project rules
- [.github/copilot-instructions.md](.github/copilot-instructions.md) — Copilot/Codex pointer
- [.github/copilot-instructions.md](.github/copilot-instructions.md) — ChatGPT / Codex / Copilot pointer
- [docs/platform_vision.md](docs/platform_vision.md) — roadmap
- [docs/database_backup_policy.md](docs/database_backup_policy.md) — backup rules
- [docs/prod_to_dev_sync_policy.md](docs/prod_to_dev_sync_policy.md) — sync order
- [docs/backup_layout.md](docs/backup_layout.md) — on-disk layout
- [docs/backup_verification.md](docs/backup_verification.md) — verification checks
- [docs/mariadb_discovery.md](docs/mariadb_discovery.md) — live discovery (may lag CLI; update when changing DB access)

## Common pitfalls

- **Import cycles** in `database/mariadb/` — keep shared exceptions in `errors.py`; avoid `client.py` ↔ `session.py` circular imports.
- **`CURRENT_USER()` alias** — do not alias as `current_user` in SQL passed to the MariaDB CLI (reserved-word syntax error); use a neutral alias.
- **`resolve_mariadb_target(None)`** loads `config/local.toml` when present — tests that need “offline” placeholders must pass an explicit config object or mock `try_load_mariadb_config`.
- **CLI subprocess tests** spawn a fresh interpreter — monkeypatching in-process does not affect them.
- **Root + pymysql on Fedora** often fails (unix_socket auth plugin); prefer `use_client = true` for local dev.

## What to build next (typical milestones)

- Prod→dev sync execution with `SYNC DEV` confirmation.
- Menu item 7: restore-check flow to `_restorecheck_*` only (non-destructive by default).
- Remove top-level shim modules once all imports use subpackages (`mercury.backup.*`, `mercury.core.*`).

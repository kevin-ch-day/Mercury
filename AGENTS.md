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

**Imports:** prefer subpackages (`mercury.backup.backup_runner`, `mercury.core.safety`, `mercury.logging`, `mercury.core.paths`). Use `mercury.output` for terminal writing. Do not add new top-level `src/mercury/*.py` compatibility modules.

## What Mercury is

Mercury is a **Fedora- and Windows-supported Python CLI** for MariaDB backup, disaster recovery, schema export, verification, prod→dev sync planning, Git repository transfer bundles, transfer manifests/runbooks, and **recovery deployment** of Mercury-managed artifacts onto a prepared host.

It is **not** an AI tool, web app, malware analyzer, or full workstation/OS bootstrap utility.

**Production targets:** Fedora and Windows for live operations when MariaDB tools, `config/local.toml`, and the active operator storage root (`MERCURY_DATA_V2`) are configured. Non-Fedora Linux remains seed planning/development only. Legacy `MERCURY_DATA_USB` is a retired offline archive, not a normal dependency.

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

**Seed / guarded destructive ops** is still the default for sync/deploy/restore:

- Planning, discovery, manifests, and reports are implemented.
- Live **read-only** server access works (`db ping`, `db discover`, `db inspect`, `db access`).
- **Backup writes** run when the backup environment is safe (Fedora/Windows, primary HDD-backed `backup_root`, config present). They do **not** require `dry_run=false` or `live_actions_enabled=true`.
- Prod→dev **sync**, **deploy**, and destructive restore-check cleanup require `dry_run=false`, `live_actions_enabled=true`, and confirmation (`SYNC DEV` for sync).
- Menu and CLI default backup execution when the environment is ready; use `--dry-run` or **Preview backup plan** for dry-run.
- Live execution is supported on **Fedora and Windows**; other Linux hosts remain seed/status only.
- Legacy USB is phased out of normal Doctor/dashboard validation; archive inspection uses `storage archive-status` / `archive-receipt` / `archive-remount-ro` only.

## Repository layout

```
src/mercury/
  cli.py                   # Typer entrypoint (`mercury` command)

  menu/                    # Interactive menu loop, prompts, dashboard, runners
    loop.py, runners.py, main_display.py, prompts.py, …
  terminal/                # Shared CLI formatting (format, screen, table)
  output.py                # Public terminal output re-export (`mercury.core.output`)

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

**Naming:** shared terminal helpers live in `mercury.terminal`; domain CLI output lives in `<package>/terminal/`; execution uses `*_runner.py`; feature menus use `interactive_menu.py`. Prefer canonical import paths (`mercury.backup.terminal.verify`, `mercury.database.terminal.inventory`, `mercury.core.paths`).

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
| ObsidianDroid | `obsidiandroid_core_prod` (backup-only; `_dev` not in sync scope unless configured) |

For the current Fedora milestone, Mercury actively protects only:

- `android_permission_intel`
- `erebus_threat_intel_prod`
- `scytaledroid_core_prod`
- `obsidiandroid_core_prod`

and plans prod→dev sync readiness only for:

- `erebus_threat_intel_prod` -> `erebus_threat_intel_dev`
- `scytaledroid_core_prod` -> `scytaledroid_core_dev`

Out-of-scope databases such as `gecko_research_database_*` (legacy Komodo/market-event naming), `droid_threat_intel_db_*`, and `proofpoint_cti_db_dev` may appear in live discovery for operator awareness, but they are excluded from backup/sync planning and do not count as blockers for this milestone.

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

**Operator storage backup root (primary HDD after cutover):**

```toml
[mercury]
# Backup writes use environment checks (Fedora/Windows + mounted operator root).
# dry_run / live_actions_enabled gate sync, deploy, and restore — not routine backups.
backup_root = "/mnt/MERCURY_DATA_V2/mercury_backups"

[storage]
active_write_role = "primary"
migration_state = "cutover_complete"
legacy_runtime_dependency = "none"
```

The active write mount is `/mnt/MERCURY_DATA_V2` (`MERCURY_DATA_V2`). Legacy `MERCURY_DATA_USB` is a retired offline archive only — see `docs/ops/post_cutover_storage.md`. Repo-local `backups/` are development artifacts only and do not count as production protection. Inspect roots with `mercury storage status` / `storage archive-status`.

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
mercury backup run --db <prod> --kind full           # execute when environment is ready
mercury backup run --db <prod> --kind full --dry-run # preview only
mercury backup verify --db <prod> [--update-manifest]  # verify on-disk artifacts
mercury storage status                   # primary vs legacy roots (observe-only)
mercury storage validate                 # mount/UUID checks for configured roots
mercury storage migrate-plan             # dry-run legacy→primary inventory (no copies)
mercury storage migrate-plan --update-state  # mark migration_state=planned when ready
mercury storage migrate-run              # dry-run copy preview (default; resumes via ledger)
mercury storage migrate-run --execute    # copy to primary; type MIGRATE PRIMARY
mercury storage migrate-quarantine       # dry-run: list primary conflict moves
mercury storage migrate-verify [--update-state]  # verify copy equality (no cutover)
mercury storage cutover-readiness        # read-only checklist (never switches writers)
mercury menu
```

After cutover, routine writers target the primary HDD (`active_write_role=primary`). Prefer `MERCURY_PRIMARY_MOUNT` / `MERCURY_LEGACY_MOUNT` over deprecated `MERCURY_USB_MOUNT`. Legacy USB archive commands: `storage archive-status`, `archive-receipt`, `archive-remount-ro`. Mercury HDD lifecycle: Main Menu → Mercury HDD and Storage.
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
| `MERCURY_LOCAL_CONFIG` / `plain_cli_text()` | Offline CI parity; stable Rich help asserts |
| `@pytest.mark.uses_operator_local_config` | Opt out of autouse local.toml isolation |
| `monkeypatch.setattr("mercury.core.paths.OUTPUT_DIR", tmp_path)` | File output in tests |
| `subprocess.run([sys.executable, "-m", "mercury.cli", ...])` | CLI integration (separate process) |

Full test file index: [docs/ai_extension_points.md](docs/ai_extension_points.md#test-file-index).

## Documentation map

- [README.md](README.md) — operator quick start
- [docs/ai_extension_points.md](docs/ai_extension_points.md) — **agent cookbook** (CLI, backup, DB, tests)
- [docs/ops/](docs/ops/) — Track A acceptance, post-cutover storage, Erebus capture
- [.cursor/rules/](.cursor/rules/) — Cursor rules (safety always on)
- [.github/copilot-instructions.md](.github/copilot-instructions.md) — ChatGPT / Codex / Copilot pointer
- [docs/database_backup_policy.md](docs/database_backup_policy.md) — backup rules
- [docs/prod_to_dev_sync_policy.md](docs/prod_to_dev_sync_policy.md) — sync order
- [docs/backup_layout.md](docs/backup_layout.md) — on-disk layout
- [docs/backup_verification.md](docs/backup_verification.md) — verification checks
- [docs/database_module.md](docs/database_module.md) — `mercury.database` package map

## Common pitfalls

- **Import cycles** in `database/mariadb/` — keep shared exceptions in `errors.py`; avoid `client.py` ↔ `session.py` circular imports.
- **`CURRENT_USER()` alias** — do not alias as `current_user` in SQL passed to the MariaDB CLI (reserved-word syntax error); use a neutral alias.
- **`resolve_mariadb_target(None)`** loads `config/local.toml` when present — tests that need “offline” placeholders must pass an explicit config object or mock `try_load_mariadb_config`.
- **Operator `local.toml` on the developer host** — unit tests isolate via `MERCURY_LOCAL_CONFIG` (see `tests/conftest.py`); do not branch assertions on the real file existing.
- **CLI subprocess tests** spawn a fresh interpreter — monkeypatching in-process does not affect them; use `subprocess_env()`.
- **Root + pymysql on Fedora** often fails (unix_socket auth plugin); prefer `use_client = true` for local dev.

## What to build next (typical milestones)

- Prod→dev sync execution polish with `SYNC DEV` confirmation.
- Development restore-check lane (Track A A-3-02).
- Advanced-command taxonomy labels (A-7-01).

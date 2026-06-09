# AI extension points — Mercury

Cookbook for AI agents (Cursor, ChatGPT, Codex) implementing features in this repo.

**Read first:** [AGENTS.md](../AGENTS.md) (safety policy, layout, pitfalls).

**Cursor:** rules in `.cursor/rules/*.mdc` load automatically.

**ChatGPT / Codex:** read [AGENTS.md](../AGENTS.md) and [.github/copilot-instructions.md](../.github/copilot-instructions.md).

---

## Read order for a new task

1. [AGENTS.md](../AGENTS.md) — safety + layout
2. Matching tests in `tests/test_<area>.py`
3. Implementation module(s) from the file map below
4. Policy docs in `docs/` if touching backup/sync behavior

---

## File responsibility map

| Path | Responsibility |
|------|----------------|
| `src/mercury/cli.py` | Top-level Typer app; backup/config/sync/report/env commands |
| `src/mercury/database/commands.py` | `mercury db *` / `mercury database *` commands |
| `src/mercury/menu/runners.py` | Interactive menu action runners |
| `src/mercury/menu/loop.py` | Menu read-eval loop and choice routing |
| `src/mercury/core/safety.py` | Policy constants, backup kind literals, role rules |
| `src/mercury/core/execution_policy.py` | `ExecutionPolicy`, env/config gates for live writes |
| `src/mercury/core/paths.py` | `REPO_ROOT`, config paths, output dirs |
| `src/mercury/core/runtime.py` | `operator_status()` for menu/CLI |
| `src/mercury/core/output.py` | Rich terminal helpers |
| `src/mercury/backup/backup_runner.py` | `plan_backup_execution`, `execute_backup`, dump runner |
| `src/mercury/backup/verification.py` | `verify_backup_artifacts`, `verify_backup_directory` |
| `src/mercury/backup/layout.py` | On-disk path planning |
| `src/mercury/backup/manifest.py` | `BackupManifest`, `build_backup_manifest` |
| `src/mercury/backup/dump_planner.py` | `mariadb-dump` argv (socket + TCP) |
| `src/mercury/backup/find_latest_backup.py` | Find latest backup dir on disk |
| `src/mercury/database/discovery/` | `discover("live"\|"demo")` |
| `src/mercury/database/core/catalog.py` | Platform DB catalog |
| `src/mercury/database/core/classifier.py` | `classify_database()` |
| `src/mercury/database/mariadb/session.py` | pymysql read-only helpers |
| `src/mercury/database/mariadb/client.py` | mariadb CLI read-only helpers |
| `src/mercury/reporting/protection.py` | Protection status report |
| `src/mercury/sync/sync_plan.py` | Prod→dev sync planning and execution gates |

Top-level `src/mercury/*.py` (except `cli.py`, `menu.py`) are **shims** — implement in subpackages.

---

## Recipe: add a CLI command

### Backup-family command → `cli.py`

```python
@backup_app.command("my-cmd")
def backup_my_cmd(db: str = typer.Option(..., "--db")) -> None:
    from mercury.backup.my_module import do_thing  # lazy import
    result = do_thing(db)
    from mercury.backup.my_display import print_result
    print_result(result)
```

Add tests: unit test for `do_thing`; optional subprocess test for CLI exit code.

### Database command → `database/commands.py`

Add inside `register_commands()`:

```python
@app.command("my-cmd")
def my_cmd_cmd() -> None:
    ...
```

Both `db_app` and `database_app` call `register_commands()` — one definition, two entry paths.

### Menu item → `menu/runners.py` + `menu/loop.py`

1. Implement runner in `menu/runners.py` with lazy imports
2. Wire in `menu/loop.py` → `handle_menu_choice()`
3. Update `tests/test_menu.py`

---

## Recipe: add backup behavior

1. **Safety** — call `assert_safe_backup_source(name)` before any prod backup plan
2. **Policy** — use `load_execution_policy()`; respect `live_execution_allowed()`
3. **Layout** — `build_backup_layout()` for paths
4. **Execute** — extend `execute_backup()` or call it; inject `dump_runner` in tests
5. **Artifacts** — manifest via `build_backup_manifest`, checksum via `write_checksum_file`
6. **Verify** — `verify_backup_directory()` after writes
7. **CLI** — `backup run` / `backup verify` pattern in `cli.py`
8. **Tests** — `tests/test_backup_execute.py`, `tests/test_backup_verify.py`

```python
def _fake_dump_runner(argv, env, output_path, _config):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"test\n")

execute_backup(
    "erebus_threat_intel_prod",
    BACKUP_KIND_FULL,
    execute=True,
    policy=ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=tmp_path),
    dump_runner=_fake_dump_runner,
)
```

---

## Recipe: add live read-only DB feature

1. Add query in `database/mariadb/` using `readonly_scalar` / `readonly_scalars`
2. Never use `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`
3. Support both access modes: check `config.use_client` path in `client.py` vs `session.py`
4. Return Pydantic model; display in `database/terminal/` (or domain `*_terminal.py`)
5. Wire CLI in `database/commands.py`
6. Test with `probe_fn` fake or `@pytest.mark.skipif` for real socket

---

## Recipe: add a platform database

1. `database/core/catalog.py` — new `CatalogEntry`
2. `classifier.py` — only if naming doesn't match `*_prod` / `*_dev` / shared rules
3. Tests: `tests/test_db_discover.py` (`test_classify_database`)
4. `mercury db access` will show catalog vs server presence automatically

---

## Test file index

| Area | Test file |
|------|-----------|
| Imports / smoke | `test_imports.py` |
| CLI commands | `test_cli_commands.py` |
| Environment / doctor / config | `test_environment.py` |
| Backup execute | `test_backup_execute.py` |
| Backup verify | `test_backup_verify.py`, `test_m45_verification.py` |
| MariaDB mocked session | `test_mariadb_session.py` |
| MariaDB live integration | `test_m7_mariadb_access.py`, `test_mariadb_performance.py` |
| Discovery + classifier | `test_db_discover.py` |
| Database scope + active scope | `test_database_scope.py` |
| Protection report | `test_protection_report.py` |
| Menu + display | `test_menu.py`, `test_menu_dashboard.py`, `test_menu_prompts.py` |
| Schema / manifest layout | `test_m4_schema_manifest.py` |
| Terminal tables | `test_display_table.py`, `test_display_format.py` |
| Logging | `tests/logging/test_engine.py` |

---

## Environment for agents

```bash
cd /path/to/Mercury
python -m venv .venv && source .venv/bin/activate
pip install -e ".[mariadb,dev]"
python -m pytest                    # must pass before finishing
.venv/bin/mercury db ping           # optional live check
```

Config: `config/local.toml` (gitignored). Never commit credentials.

---

## Shim → canonical import reference

| Shim (legacy) | Canonical |
|---------------|-----------|
| `mercury.safety` | `mercury.core.safety` |
| `mercury.paths` | `mercury.core.paths` |
| `mercury.runtime` | `mercury.core.runtime` |
| `mercury.execution_policy` | `mercury.core.execution_policy` |
| `mercury.output` | `mercury.core.output` |
| `mercury.display_format` | `mercury.terminal.format` |
| `mercury.display_screen` | `mercury.terminal.screen` |
| `mercury.display_table` | `mercury.terminal.table` |
| `mercury.terminal_format` | `mercury.terminal.format` |
| `mercury.terminal_screen` | `mercury.terminal.screen` |
| `mercury.menu` | `mercury.menu.runners` |
| `mercury.menu_runners` | `mercury.menu.runners` |
| `mercury.menu_display` | `mercury.menu.main_display` |
| `mercury.menu_prompts` | `mercury.menu.prompts` |
| `mercury.database.service` | `mercury.database.facade` |
| `mercury.database.cli` | `mercury.db_commands` (shim: `mercury.database.commands`) |
| `mercury.database.*_terminal` | `mercury.database.terminal.*` |
| `mercury.backup_execute` | `mercury.backup.backup_runner` |
| `mercury.backup_display` | `mercury.backup.terminal.plan` |
| `mercury.backup_execute_display` | `mercury.backup.terminal.runner` |
| `mercury.verify_display` | `mercury.backup.terminal.verify` |
| `mercury.plan_display` | `mercury.reporting.terminal.plan` |
| `mercury.log_display` | `mercury.logging.terminal.status` |
| `mercury.backup.*_terminal` | `mercury.backup.terminal.*` |
| `mercury.sync.*_terminal` | `mercury.sync.terminal.*` |
| `mercury.restore.*_terminal` | `mercury.restore.terminal.*` |
| `mercury.env.check_terminal` | `mercury.env.terminal.check` |
| `mercury.backup_list` | `mercury.backup.on_disk_index` |
| `mercury.verification` | `mercury.backup.verification` |
| `mercury.protection_report` | `mercury.reporting.protection` |
| `mercury.env_probe` | `mercury.env.probe` |
| `mercury.logging_engine` | `mercury.logging` |
| `mercury.log_events` | `mercury.logging.events` |
| `mercury.log_display` | `mercury.logging.terminal.status` |
| `mercury.sync_plan` (top-level) | `mercury.sync.sync_plan` |

New code in `src/` and `tests/` must use canonical paths. Shims remain for external callers only.

---

## Anti-patterns (do not)

- Weakening safety checks or skipping `assert_safe_backup_source`
- Defaulting to live execution without policy gates
- Adding DDL/DML to “probe” or “inspect” code paths
- New top-level shim modules instead of subpackages
- Large refactors mixed with feature work
- Committing `config/local.toml` or passwords
- Assuming pymysql root works on Fedora (use `use_client` + socket)

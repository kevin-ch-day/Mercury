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
| `src/mercury/database/cli.py` | `mercury db *` / `mercury database *` commands |
| `src/mercury/menu.py` | Interactive menu; wire items to real commands when ready |
| `src/mercury/core/safety.py` | Policy constants, backup kind literals, role rules |
| `src/mercury/core/execution_policy.py` | `ExecutionPolicy`, env/config gates for live writes |
| `src/mercury/core/paths.py` | `REPO_ROOT`, config paths, output dirs |
| `src/mercury/core/runtime.py` | `operator_status()` for menu/CLI |
| `src/mercury/core/output.py` | Rich terminal helpers |
| `src/mercury/backup/execute.py` | `plan_backup_execution`, `execute_backup`, dump runner |
| `src/mercury/backup/verification.py` | `verify_backup_artifacts`, `verify_backup_directory` |
| `src/mercury/backup/layout.py` | On-disk path planning |
| `src/mercury/backup/manifest.py` | `BackupManifest`, `build_backup_manifest` |
| `src/mercury/backup/dump_planner.py` | `mariadb-dump` argv (socket + TCP) |
| `src/mercury/backup/locate.py` | Find latest backup dir on disk |
| `src/mercury/database/discovery/` | `discover("live"\|"demo")` |
| `src/mercury/database/core/catalog.py` | Platform DB catalog |
| `src/mercury/database/core/classifier.py` | `classify_database()` |
| `src/mercury/database/mariadb/session.py` | pymysql read-only helpers |
| `src/mercury/database/mariadb/client.py` | mariadb CLI read-only helpers |
| `src/mercury/reporting/protection.py` | Protection status report |
| `src/mercury/sync/plan.py` | Prod→dev sync planning (execution TBD) |

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

### Database command → `database/cli.py`

Add inside `register_commands()`:

```python
@app.command("my-cmd")
def my_cmd_cmd() -> None:
    ...
```

Both `db_app` and `database_app` call `register_commands()` — one definition, two entry paths.

### Menu item → `menu.py`

1. Implement `run_my_feature()` with lazy imports
2. Wire in `handle_menu_choice()` 
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
4. Return Pydantic model; display in `database/display_*.py`
5. Wire CLI in `database/cli.py`
6. Test with `probe_fn` fake or `@pytest.mark.skipif` for real socket

---

## Recipe: add a platform database

1. `database/core/catalog.py` — new `CatalogEntry`
2. `classifier.py` — only if naming doesn't match `*_prod` / `*_dev` / shared rules
3. Tests: `tests/test_db_classifier.py`
4. `mercury db access` will show catalog vs server presence automatically

---

## Test file index

| Area | Test file |
|------|-----------|
| Imports / smoke | `test_imports.py` |
| CLI seed | `test_cli_seed.py`, `test_cli_m4.py` |
| Backup execute | `test_backup_execute.py` |
| Backup verify | `test_backup_verify.py`, `test_m45_verification.py` |
| MariaDB live | `test_m5_mariadb_live.py`, `test_m6_mariadb_connect.py`, `test_m7_mariadb_access.py` |
| Discovery | `test_db_discover.py`, `test_database_module.py` |
| Classifier | `test_db_classifier.py` |
| Protection report | `test_protection_report.py` |
| Menu | `test_menu.py` |
| Config init | `test_config_init.py` |

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
| `mercury.backup_execute` | `mercury.backup.execute` |
| `mercury.verification` | `mercury.backup.verification` |
| `mercury.protection_report` | `mercury.reporting.protection` |
| `mercury.env_probe` | `mercury.env.probe` |

New code should use canonical paths. Shims exist for older tests and imports.

---

## Anti-patterns (do not)

- Weakening safety checks or skipping `assert_safe_backup_source`
- Defaulting to live execution without policy gates
- Adding DDL/DML to “probe” or “inspect” code paths
- New top-level shim modules instead of subpackages
- Large refactors mixed with feature work
- Committing `config/local.toml` or passwords
- Assuming pymysql root works on Fedora (use `use_client` + socket)

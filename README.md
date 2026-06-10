# Mercury

**Mercury** is a Fedora-first **operations utility** for MariaDB backup, verification, restore-check, production-to-development sync, Git repository transfer bundles, transfer manifests/runbooks, and **recovery deployment** of Mercury-managed database and repository artifacts onto a prepared Fedora host.

For the current Fedora milestone, it protects the active source databases `android_permission_intel`, `erebus_threat_intel_prod`, `scytaledroid_core_prod`, and `obsidiandroid_core_prod`, manages the dev sync targets `erebus_threat_intel_dev` and `scytaledroid_core_dev` as disposable refresh targets, and can inventory configured Git repositories plus write explicit Git bundles to the USB transfer media. It is not an AI tool, web app, or full Fedora workstation provisioning tool.

Windows and non-Fedora Linux are for **seed planning / development** only; live Mercury operations target **Fedora**.

## Current v1 status

- Database package: complete
- Repository package: complete with warnings
- Transfer package: complete
- Prod-to-dev sync: executed
- Dirty repo warnings: recorded truthfully in manifests/state output
- Recovery deployment: deploy verified USB backups and repo bundles onto a prepared Fedora host (not full OS bootstrap)
- Full workstation provisioning: out of scope for Mercury

## Hard policy

- Backup **production / source-of-truth** only (`*_prod`, `android_permission_intel`).
- **Never** back up `*_dev` by default — dev DBs are disposable refresh targets rebuilt from verified source backups when needed.
- **Never** drop or overwrite `*_prod`; never restore into prod by default.
- Back up and verify the source before any prod→dev sync; the point is to protect the source state before refreshing dev, and dev sync will require typing `SYNC DEV`.
- A database is not **protected** until backup verification passes (manifest + checksum).

## Quick start

```bash
cd /path/to/Mercury
./run.sh                         # interactive menu (creates .venv on first run)
```

The menu groups actions by task, pauses after each screen, and accepts `q` to quit.

```bash
# Or manual setup:
python -m venv .venv && source .venv/bin/activate
pip install -e ".[mariadb,dev]"

mercury config init
mercury db ping                    # read-only server probe
mercury db discover                # live inventory (needs config/local.toml)
mercury status --live              # protection snapshot from live server
mercury backup run --db erebus_threat_intel_prod --kind full   # dry-run plan
mercury repo status
mercury transfer status --live
python -m pytest
```

If `./run.sh` cannot reach PyPI temporarily, Mercury should still be started
from an already-synced virtualenv with:

```bash
MERCURY_SKIP_SYNC=1 ./run.sh
```

Fedora offline fallback:

```bash
sudo dnf install python3-hatchling python3-pydantic python3-rich python3-typer python3-pymysql python3-pytest mariadb
rm -rf .venv
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --no-build-isolation -e ".[mariadb,dev]"
```

## CLI commands

### Environment and config

```bash
mercury env probe [--check-db]
mercury config init
mercury config validate [--demo]
mercury status [--live] [--save]
mercury menu
```

### Database (read-only live access)

```bash
mercury db ping
mercury db discover [--demo]
mercury db inspect --name erebus_threat_intel_prod
mercury db active
mercury db access                    # catalog vs server presence
mercury db pairs
mercury db classify --name <db>
mercury db validate [--demo]
mercury database summary [--demo]
```

### Backup

```bash
mercury backup plan [--demo]             # live inventory when configured
mercury backup schema-plan [--demo]
mercury backup run --db <prod> --kind full|schema_only [--execute]
mercury backup batch [--kind full|schema_only] [--execute] [--demo]
mercury backup all [--kind full|schema_only] [--execute] [--demo]
mercury backup verify --db <prod> [--path DIR] [--update-manifest]
mercury backup verify-all [--update-manifest] [--demo]
mercury backup status [--db <source>] [--demo]
mercury backup bundle [--db <source>] [--demo] [--execute]
mercury backup verify-plan --demo
mercury backup manifest-preview --db <prod> --kind full|schema_only
mercury backup list [--demo]
mercury report preview --db <prod> --kind full|schema_only
```

`backup run --execute` and `backup batch --execute` require `[mercury] dry_run = false` and `live_actions_enabled = true` in `config/local.toml`.
For the current Fedora milestone, live backup execution also requires:
- Fedora as the runtime host
- the mounted USB-backed root under `/mnt/MERCURY_DATA_USB/mercury_backups`

Repo-local `backups/` remains development-only and does not count as production protection in live/operator mode.
`backup status` reports the latest protection state for active source databases using on-disk manifests and verification checks. `backup bundle --execute` writes database transfer manifests and restore notes to the configured USB-backed manifest/runbook paths.

### Sync

```bash
mercury sync plan [--demo]
mercury sync readiness [--live]
mercury sync run [--live] [--source <prod>] [--target <dev>] [--execute]
mercury sync all [--live] [--execute]
```

`sync run --execute` restores verified backups into disposable dev targets. With no filter it processes all ready pairs; `--source` or `--target` limits execution to one pair. `sync all` is the explicit batch alias. For the current milestone, sync readiness only applies to `erebus_threat_intel_prod -> erebus_threat_intel_dev` and `scytaledroid_core_prod -> scytaledroid_core_dev`. `android_permission_intel` and `obsidiandroid_core_prod` are backup-only and do not participate in sync pairing unless dev targets are explicitly configured. Requires live mode and typing `SYNC DEV`.

### Repository transfer

```bash
mercury repo init-config [--force]
mercury repo status [--verbose]
mercury repo bundle [--repo mercury] [--execute]
```

`repo init-config` writes `config/repos.toml` from the known Fedora desktop repo paths. `repo status` is read-only and reports configured repo path, branch, commit, remote, clean/dirty state, untracked count, and upstream ahead/behind status when available. `repo bundle --execute` writes Git bundles plus repo manifests and short restore notes under the USB-backed paths configured in `config/local.toml`. Mercury keeps one current verified bundle set per repo and prunes older repo bundle artifacts only after the replacement bundle is written and verified successfully.

### Combined transfer

```bash
mercury transfer status [--live]
mercury transfer write [--live] [--execute]
```

`transfer status` shows one combined database + repository handoff summary. `transfer write --execute` writes one aggregate transfer manifest and one aggregate runbook to the USB-backed manifest/runbook paths.

### Restore-check

```bash
mercury restore-check plan --db <prod>
mercury restore-check run --db <prod> [--execute]
mercury restore-check cleanup [--execute]
```

Restore-check runs into temporary `_restorecheck_*` databases only. Use `cleanup --execute` to drop them after validation.
Successful restore-check runs now auto-drop the temporary `_restorecheck_*` database. If import or validation fails, Mercury preserves the temp database for debugging and prints the cleanup command.

### Recovery deployment

Mercury can **import verified USB database backups** and **restore configured Git repositories** onto a prepared Fedora/MariaDB host after hardware loss or migration. This is Mercury-managed artifact recovery — not Fedora package installation, systemd setup, or full workstation bootstrap.

```bash
mercury deploy status
mercury deploy db --dry-run
mercury deploy repos --from-github --dry-run
mercury deploy system --dry-run
mercury deploy use-cases
```

Live deploy requires the same gates as backup execution (`dry_run = false`, `live_actions_enabled = true`, Fedora host, USB-backed backup root). Menu: option **8 → Deploy to This System**.

## Setup

### Local Fedora (unix socket — no password)

```toml
# config/local.toml
[mercury]
backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"
log_dir = "/mnt/MERCURY_DATA_USB/mercury_logs"
repo_backup_root = "/mnt/MERCURY_DATA_USB/mercury_repo_backups"
manifest_dir = "/mnt/MERCURY_DATA_USB/mercury_manifests"
runbook_dir = "/mnt/MERCURY_DATA_USB/mercury_runbooks"
dry_run = true
live_actions_enabled = false

[mariadb]
user = "root"
use_client = true
unix_socket = "/var/lib/mysql/mysql.sock"
```

### Remote / password auth

```toml
[mariadb]
host = "127.0.0.1"
port = 3306
user = "mercury_readonly"
password_env = "MERCURY_MARIADB_PASSWORD"
```

```bash
export MERCURY_MARIADB_PASSWORD='your-password'
```

## Project layout

```
src/mercury/
  cli.py, menu.py
  core/          paths, safety, runtime, execution policy
  backup/        plan, execute, verify, manifests
  config/        settings, init
  database/      discovery, MariaDB, classification
  env/           environment probe
  reporting/     protection status, previews
  transfer/      combined database + repository transfer manifest/runbook
  sync/          prod→dev planning, readiness, execution
  restore/       restore-check planning and execution
  deploy/        recovery deployment of verified USB DB backups and repo bundles
```

Top-level compatibility shims under `src/mercury/*.py` remain for external callers only. New code should use the canonical subpackages (`mercury.backup.*`, `mercury.core.*`, `mercury.database.*`, `mercury.menu.*`, `mercury.terminal.*`).

See [AGENTS.md](AGENTS.md) for contributor/agent guidance. See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request expectations.

## For AI coding agents

| Resource | Purpose |
|----------|---------|
| [AGENTS.md](AGENTS.md) | Safety policy, layout, workflow |
| [docs/ai_extension_points.md](docs/ai_extension_points.md) | Recipes: add CLI, backup, DB features, tests |
| [.cursor/rules/](.cursor/rules/) | Cursor rules (safety always on) |
| [.github/copilot-instructions.md](.github/copilot-instructions.md) | ChatGPT, Codex, and Copilot pointer |

## Python API

```python
from mercury.database import discover, print_inventory
from mercury.database.core import classify_database

print_inventory(discover("demo"))
```

See [database module](docs/database_module.md).

## Docs

- [Platform vision](docs/platform_vision.md)
- [Database module](docs/database_module.md)
- [Database backup policy](docs/database_backup_policy.md)
- [Prod-to-dev sync policy](docs/prod_to_dev_sync_policy.md)
- [Backup layout](docs/backup_layout.md)
- [Disaster recovery runbook](docs/disaster_recovery_runbook.md)
- [Backup verification](docs/backup_verification.md)
- [MariaDB live discovery](docs/mariadb_discovery.md)
- [Mercury v1 checklist](docs/mercury_v1_checklist.md)

## License

See [LICENSE](LICENSE).

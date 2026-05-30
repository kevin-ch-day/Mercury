# Mercury

**Mercury** is a Fedora-first **operations utility** for database backup, disaster recovery, schema-only exports, and production-to-development sync on the Android security research platform.

It protects **Erebus**, **android_permission_intel**, **ScytaleDroid**, **ObsidianDroid** (gecko DBs), and related MariaDB databases. It is not an AI tool, web app, or repo-status utility.

Windows is for **seed development** only; production use targets **Fedora**.

## Hard policy

- Backup **production / source-of-truth** only (`*_prod`, `android_permission_intel`).
- **Never** back up `*_dev` by default — dev DBs are rebuilt from verified prod backups.
- **Never** drop or overwrite `*_prod`; never restore into prod by default.
- Backup and verify prod before any prod→dev sync; dev sync will require typing `SYNC DEV`.
- A database is not **protected** until backup verification passes (manifest + checksum).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[mariadb,dev]"

mercury config init
mercury db ping                    # read-only server probe
mercury db discover                # live inventory (needs config/local.toml)
mercury status --live              # protection snapshot from live server
mercury backup run --db erebus_threat_intel_prod --kind full   # dry-run plan
python -m pytest
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
mercury db access                    # catalog vs server presence
mercury db pairs
mercury db classify --name <db>
mercury db validate [--demo]
mercury database summary [--demo]
```

### Backup

```bash
mercury backup plan --demo
mercury backup schema-plan --demo
mercury backup run --db <prod> --kind full|schema_only [--execute]
mercury backup verify --db <prod> [--path DIR] [--update-manifest]
mercury backup verify-plan --demo
mercury backup manifest-preview --db <prod> --kind full|schema_only
mercury backup list --demo
mercury report preview --db <prod> --kind full|schema_only
```

`backup run --execute` requires `[mercury] dry_run = false` and `live_actions_enabled = true` in `config/local.toml`.

### Sync (planning only)

```bash
mercury sync plan --demo
```

## Setup

### Local Fedora (unix socket — no password)

```toml
# config/local.toml
[mariadb]
user = "root"
use_client = true
unix_socket = "/var/lib/mysql/mysql.sock"

[mercury]
dry_run = true
live_actions_enabled = false
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
  sync/          prod→dev planning
```

See [AGENTS.md](AGENTS.md) for contributor/agent guidance. See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request expectations.

## For AI coding agents

| Resource | Purpose |
|----------|---------|
| [AGENTS.md](AGENTS.md) | Safety policy, layout, workflow |
| [docs/ai_extension_points.md](docs/ai_extension_points.md) | Recipes: add CLI, backup, DB features, tests |
| [.cursor/rules/](.cursor/rules/) | Cursor rules (safety always on) |
| [CLAUDE.md](CLAUDE.md) / [.github/copilot-instructions.md](.github/copilot-instructions.md) | Pointers for other agent tools |

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

## License

See [LICENSE](LICENSE).

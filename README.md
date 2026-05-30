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

## Seed commands (safe — no DB server required)

```bash
pip install -e ".[mariadb,dev]"

mercury status              # what is protected, prod→dev pairs, action items
mercury status --save       # also writes output/protection_status.txt
mercury config init         # create config/*.toml from examples
mercury config validate     # policy check on config (--demo for full catalog)
mercury env probe
mercury db discover              # live read-only SHOW DATABASES (needs config/local.toml)
mercury db discover --demo       # catalog only, no server
mercury database summary --demo  # same module; counts + backup sources
mercury db pairs              # prod→dev mapping
mercury db classify --name erebus_threat_intel_prod
mercury db validate --demo    # policy check against catalog inventory
mercury backup plan --demo
mercury backup plan --demo --sample-manifest
mercury backup schema-plan --demo
mercury backup manifest-preview --db erebus_threat_intel_prod --kind schema_only
mercury backup manifest-preview --db erebus_threat_intel_prod --kind full
mercury backup verify-plan --demo
mercury backup list --demo
mercury report preview --db erebus_threat_intel_prod --kind full
mercury report preview --db erebus_threat_intel_prod --kind schema_only
mercury sync plan --demo
mercury menu
```

Also: `python -m mercury.cli …` (same commands).

Without `--demo`, `db discover` uses **`config/local.toml`** and `MERCURY_MARIADB_PASSWORD` for live read-only discovery. `backup plan` still requires `--demo` in seed mode.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate   # Fedora

pip install -e ".[mariadb,dev]"
copy config\databases.example.toml config\databases.toml   # optional
copy config\local.example.toml config\local.toml         # for live discover
set MERCURY_MARIADB_PASSWORD=your_password             # Windows; use export on Linux
```

## Tests

```bash
python -m pytest
```

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

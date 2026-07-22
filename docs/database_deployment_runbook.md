# Database recovery deployment runbook

Import verified Mercury operator-storage backups onto a **prepared Fedora/MariaDB host** after migration or hardware loss.

Mercury recovery deployment assumes the host is already set up (MariaDB running, canonical HDD/operator storage mounted). It is **not** prod→dev sync and **not** full workstation bootstrap.

## When to use

- Prod/shared databases are **missing** on the new server.
- Mercury operator storage is mounted (this deployment: HDD at `/mnt/MERCURY_DATA_V2`).
- Backups are **verified** (manifest + checksum + size).

This lane is **not** prod→dev sync. It recreates protected databases on the local host from artifact files.

## Safety defaults

- **Dry-run is default** — no imports unless live gates and explicit `--execute` / menu confirmation.
- **Never drops** existing databases unless overwrite/drop modes are explicitly enabled (default off).
- **Skips existing** same-name databases on live deploy (default).
- **Repo-local** `backups/` cannot be used for live deployment.
- **No typed phrase** confirmations — simple `y/n` only for live menu deploy.

## Quick start (Neptune)

```bash
cd /home/linuxadmin/GitHub/Mercury
./run.sh doctor
./run.sh doctor --repair-plan
./run.sh deploy db --dry-run
./run.sh menu    # option [8] Deploy databases to this system
```

One-shot host repair (review first, requires sudo):

```bash
sudo ./scripts/repair-neptune.sh
```

## CLI

```bash
./run.sh deploy db --dry-run              # plan latest verified set
./run.sh deploy db --plan-only            # same as dry-run
./run.sh deploy db --database erebus_threat_intel_prod --dry-run
./run.sh deploy db --all --dry-run
./run.sh deploy db --execute              # gated live import (requires config)
```

Live execution requires in `config/local.toml`:

```toml
[mercury]
dry_run = false
live_actions_enabled = true
backup_root = "/mnt/MERCURY_DATA_V2/mercury_backups"
```

## Workflow phases

1. **Preflight** — config, operator-storage root, MariaDB connectivity/privileges, verified backups, disk space, existing DB overlap.
2. **Backup selection** — latest verified full backup per protected source (v1).
3. **Plan** — shows target host, user, candidates, and shell/SQL steps.
4. **Confirmation** — dry-run: none; live menu: `y/n`.
5. **Execution** — checksum verify → `CREATE DATABASE IF NOT EXISTS` → `gunzip -c dump | mariadb db`.
6. **Verification** — table count/size; row-count compare when manifest includes inventory.
7. **Summary** — deployed/skipped/failed counts and report path under `mercury_restore_checks/deployments/`.

## Protected sources

Mercury deploys only approved backup-source names from the catalog (`*_prod`, `android_permission_intel`).

## Limitations (v1)

- Latest verified set only (no per-date picker in menu yet).
- Row-count verification is basic unless backup manifests capture `row_counts`.
- Overwrite requires both `allow_overwrite_database` and `allow_drop_database` (CLI flags planned).

## Next steps after deploy

```bash
./run.sh db inventory
./run.sh doctor
./run.sh backup verify --db <name>
```

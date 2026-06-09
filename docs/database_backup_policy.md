# Database backup policy

For the current Fedora milestone, Mercury protects the active source MariaDB databases `erebus_threat_intel_prod`, `scytaledroid_core_prod`, and shared `android_permission_intel`.

Preservation targets are the source databases above. The `_dev` databases are refresh targets: Mercury keeps them available for later prod-to-dev sync, but does not preserve them by default.

## Full vs schema-only

| Type | Purpose | Backup source? |
|------|---------|----------------|
| **Full logical** | Schema + data — DR and prod-to-dev sync | Yes, for `*_prod` and shared authority |
| **Schema-only** | Structure only — review and empty DB shells | Same sources as full; **not** a substitute for full backups |

Schema-only uses planned `*.schema.sql.gz` files. Full verified backups are **required** before any prod-to-dev sync.

## What to back up

| Pattern / name | Role | Backup source |
|----------------|------|---------------|
| `erebus_threat_intel_prod` | Production | Yes |
| `scytaledroid_core_prod` | Production | Yes |
| `android_permission_intel` | Shared authority | Yes |
| `erebus_threat_intel_dev` | Development | **No** — disposable refresh target |
| `scytaledroid_core_dev` | Development | **No** — disposable refresh target |
| `_restorecheck_*` | Restore-check temp | **No** |
| Other `*_prod` / `*_dev` | Out of current milestone scope | **No** (visible for review; excluded by default) |
| Other | Unknown | **No** (manual review) |

## Rules

1. **Back up only the active production sources and designated shared authority database.**
2. **Never back up `*_dev` databases** — they are disposable refresh targets, rebuilt from verified source backups when needed.
3. **Never drop or overwrite `*_prod`.**
4. **Restore-check databases** are temporary; exclude from all backup plans.
5. **Unknown names** require manual review.
6. **Repo-local backups are development artifacts only** — they do not count as production protection in live/operator mode.

## Verification and protection

- A backup is **not protected** until verification passes (manifest + checksum + size + role checks).
- **Schema-only** verified backups are useful for schema review and empty shells; they are **not** sufficient for full DR or prod-to-dev sync.
- **Full verified** backups are required before prod-to-dev sync so Mercury can protect the source state before refreshing dev.

## Seed mode (M4 / M4.5)

- `mercury backup schema-plan --demo` — schema-only dry-run plan
- `mercury backup manifest-preview` — JSON preview only; no files written
- `mercury backup verify-plan --demo` — verification process model; no real file checks
- `mercury backup list --demo` — demo planned records only
- `mercury report preview --db <name> --kind full|schema_only` — Markdown report preview
- No `mariadb-dump` execution, no live connections, `live_actions = false`

For live execution, Mercury requires:

- `[mercury] dry_run = false`
- `[mercury] live_actions_enabled = true`
- a Fedora runtime host
- a mounted USB-backed root under `/mnt/MERCURY_DATA_USB/mercury_backups`

## Retention

- Database retention is intentionally conservative in v1.1.
- Mercury does not yet auto-prune database backup files because the current layout tracks one latest manifest/checksum/report set inside a shared database/day directory.
- The accepted future policy is to keep the last 2 verified full backup sets per source database and prune only after a newer backup verifies successfully.
- That database retention policy should be implemented after a layout migration to one unique backup-set directory per backup ID or timestamp.

Repository retention is different:

- Mercury keeps 1 current verified Git bundle set per configured repo.
- Older repo bundle artifacts are pruned only after the replacement bundle is written and `git bundle verify` succeeds.

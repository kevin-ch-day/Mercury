# Database backup policy

For the current Fedora milestone, Mercury protects the active source MariaDB databases `erebus_threat_intel_prod`, `scytaledroid_core_prod`, `obsidiandroid_core_prod`, and shared `android_permission_intel`.

Preservation targets are the source databases above. The `_dev` databases are refresh targets: Mercury keeps them available for later prod-to-dev sync, but does not preserve them by default.

## Full vs schema-only

| Type | Purpose | Backup source? |
|------|---------|----------------|
| **Full logical** | Schema + data — DR and prod-to-dev sync | Yes, for `*_prod` and shared authority |
| **Schema-only** | Structure only — review and empty DB shells | Same sources as full; **not** a substitute for full backups |

Schema-only uses planned `*.schema.sql.gz` files. Full verified backups are **required** before any prod-to-dev sync.

## Backup Operations menu

| Option | Meaning |
|--------|---------|
| **[2] Run full backup now** | Back up all configured **production** databases, **automatically verify** the newly written backup IDs from that run, then optionally back up and verify development databases for migration recovery. |
| **[3] Back up production databases** | Production-only write workflow (operator still runs **[4] Verify source backups** afterward unless using full backup). |
| **[9] Back up development databases** | Development-only optional recovery capture. Not part of routine production protection or the default handoff package. |

A dump exit status alone is not success for full backup: newly written production artifacts must verify before the operation is `PASS`.

Routine verified backups remain separate from sealed migration packages (for example Phase 3B `20260722T055400Z_phase3b`) until restore-check and handoff packaging explicitly promote them.

CLI parity:

| Command | Meaning |
|---------|---------|
| `mercury backup full` | Same contract as menu **[2]** (write + verify exact IDs + optional `--include-dev`) |
| `mercury backup batch` / `all` | Write active sources; **`--verify` is default** on execute (`--no-verify` to skip) |
| `mercury backup run` | Single-database write (verify separately or via batch/full) |

## What to back up

| Pattern / name | Role | Backup source |
|----------------|------|---------------|
| `erebus_threat_intel_prod` | Production | Yes |
| `scytaledroid_core_prod` | Production | Yes |
| `obsidiandroid_core_prod` | Production (ObsidianDroid) | Yes (backup-only; no automatic sync) |
| `android_permission_intel` | Shared authority | Yes |
| `erebus_threat_intel_dev` | Development | **No by default** — optional recovery via Backup Operations [9] / full-backup optional prompt only |
| `scytaledroid_core_dev` | Development | **No by default** — optional recovery via Backup Operations [9] / full-backup optional prompt only |
| `_restorecheck_*` | Restore-check temp | **No** |
| Other `*_prod` / `*_dev` | Out of current milestone scope (e.g. `gecko_research_database_*`, Komodo/market-event DBs) | **No** (visible for review; excluded by default) |
| Other | Unknown | **No** (manual review) |

## Rules

1. **Back up only the active production sources and designated shared authority database.**
2. **Do not back up `*_dev` databases by default** — they are disposable refresh targets. Optional development recovery backups are an explicit menu path only and are never part of the default handoff package.
3. **Never drop or overwrite `*_prod`.**
4. **Restore-check databases** are temporary; exclude from all backup plans.
5. **Unknown names** require manual review.
6. **Repo-local backups are development artifacts only** — they do not count as production protection in live/operator mode.

## Verification and protection

- A backup is **not protected** until verification passes (manifest + checksum + size + role checks).
- **Schema-only** verified backups are useful for schema review and empty shells; they are **not** sufficient for full DR or prod-to-dev sync.
- **Full verified** backups are required before prod-to-dev sync so Mercury can protect the source state before refreshing dev.

### Live-presence status terms

For a source included in a live protection report, `absent` means the source
schema was not present on the MariaDB server that was probed. `missing` means
the source schema was present on that server, but no qualifying backup package
was found. These are deliberately different states: an absent schema does not
become a missing backup merely because it is listed in the configured source
catalog.

## Seed mode (M4 / M4.5)

- `mercury backup schema-plan --demo` — schema-only dry-run plan
- `mercury backup manifest-preview` — JSON preview only; no files written
- `mercury backup verify-plan --demo` — verification process model; no real file checks
- `mercury backup list --demo` — demo planned records only
- `mercury report preview --db <name> --kind full|schema_only` — Markdown report preview
- No `mariadb-dump` execution, no live connections, `live_actions = false`

## Live backup execution

Backup writes are allowed when Mercury’s backup environment checks pass:

- Fedora or Windows runtime host
- `config/local.toml` present with operator-storage `backup_root`
- mounted USB layout (`mercury_backups/`, `mercury_logs/`, … under configured `usb_mount` or Linux default `/mnt/MERCURY_DATA_USB`)
- sufficient free space on the backup root
- source database present on MariaDB (missing protected sources are refused, not silently skipped)

If the USB drive is plugged in but unmounted, run `./run.sh doctor --repair-plan` (Linux) for mount and directory setup commands.

Use `--dry-run` on CLI backup commands or **Preview backup plan** in the menu to plan without writing files.

**Verification** is safe by default and may update manifests/ledger when checks pass.

## Destructive live execution

Prod→dev sync, deploy, restore-check cleanup, and similar destructive actions additionally require:

- `[mercury] dry_run = false`
- `[mercury] live_actions_enabled = true`
- confirmation phrases where applicable (`SYNC DEV` for sync)

## Retention

- Database retention is intentionally conservative in v1.1.
- Mercury does not yet auto-prune database backup files because the current layout tracks one latest manifest/checksum/report set inside a shared database/day directory.
- The accepted future policy is to keep the last 2 verified full backup sets per source database and prune only after a newer backup verifies successfully.
- That database retention policy should be implemented after a layout migration to one unique backup-set directory per backup ID or timestamp.

Repository retention is different:

- Mercury keeps 1 current verified Git bundle set per configured repo.
- Older repo bundle artifacts are pruned only after the replacement bundle is written and `git bundle verify` succeeds.

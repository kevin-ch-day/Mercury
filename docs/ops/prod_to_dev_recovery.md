# Prod→Dev Recovery Runbook

## Purpose

`*_prod` is the authoritative source. Approved `*_dev` databases are disposable
development clones that Mercury can rebuild from a verified production backup.
Ordinary prod→dev recovery modifies only an approved development target; it does
not modify production.

Current approved pairs are:

```text
android_permission_intel     → android_permission_intel_dev
erebus_threat_intel_prod     → erebus_threat_intel_dev
scytaledroid_core_prod       → scytaledroid_core_dev
```

Refresh Permission Intel first. Erebus and ScytaleDroid development clones
depend on `android_permission_intel_dev`. Mercury rewrites approved
`android_permission_intel` identifiers in those import streams to
`android_permission_intel_dev`; the original verified backup is not modified.

## Normal workflow

1. Create or select a fresh verified full production backup.
2. Confirm its restore-requirements contract is compatible with the active Mercury importer.
3. Confirm Sync shows `Restore credentials  Configured · <identity>`.
4. Run the read-only restore privilege preflight for the intended pair.
5. Review the full `source → target` database names.
6. Run one-pair sync and accept the default-no confirmation only after review.
7. Mercury drops and recreates only that approved `_dev` target.
8. Mercury streams the exact verified production artifact into the new target.
9. Run `mercury sync verify --live` to reconcile tables and views to the selected backup baseline.
10. Review the preflight receipt and the sync ledger.

Use one-pair sync for a repair, investigation, or first validation: it produces
the clearest evidence. The batch command remains available for routine work only
after every pair is independently understood.

## Restore credentials

Ordinary live prod→dev recovery requires a dedicated credential lane. It never
falls back to the general `[mariadb]` operator/source account.

```toml
[mariadb_restore]
user = "mercury_dev_restore"
unix_socket = "/var/lib/mysql/mysql.sock"
use_client = true
password_file = "~/.config/mercury/mercury_dev_restore.password"
```

The password file remains local, untracked, and owner-private (for example
`0600`). The dedicated identity should be limited to the approved `_dev` schemas
and have no production write authority.

## Proposed `mercury_dev_restore` grants (do not apply automatically)

Mercury does not apply DCL. A DBA should grant only what ordinary rewritten
dev sync needs:

```sql
-- Recreate approved disposable clones (existing host already uses this pattern).
GRANT ALL PRIVILEGES ON `android_permission_intel_dev`.* TO 'mercury_dev_restore'@'localhost';
GRANT ALL PRIVILEGES ON `erebus_threat_intel_dev`.* TO 'mercury_dev_restore'@'localhost';
GRANT ALL PRIVILEGES ON `scytaledroid_core_dev`.* TO 'mercury_dev_restore'@'localhost';

-- Post-rewrite Erebus/Scytale views select from the disposable PI clone.
GRANT SELECT ON `android_permission_intel_dev`.* TO 'mercury_dev_restore'@'localhost';
```

Do **not** grant `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER` on
`android_permission_intel` or any `*_prod` schema. Ordinary rewritten sync
should not require `SELECT` on production Permission Intel.

## Expected safety refusals

| Condition | Meaning |
|---|---|
| Restore credentials unavailable | No target was modified. Configure or repair `[mariadb_restore]`; do not use the general credential as a workaround. |
| Missing capability, such as `ALTER ROUTINE` | Preflight refused before `DROP DATABASE`; have the DBA review only that concrete target-scoped requirement. |
| Old or incompatible artifact contract | Create a fresh governed production backup; do not alter historical manifests. |
| Artifact or identity mismatch | Preflight is not valid for the intended import. Re-run the read-only preflight after correcting the mismatch. |

A refusal is a successful safety outcome, not a request to bypass Mercury.

## Evidence

On the operator mount:

```text
.mercury_control/restore_preflights/
mercury_state/sync_events.csv
```

Preflight receipts bind the source/target, backup identity and checksum, restore
identity, and required/effective capabilities while recording that the target was
untouched. `sync_events.csv` records sync execution outcome and selected backup
directory. Backup manifests and checksums remain alongside each backup artifact.

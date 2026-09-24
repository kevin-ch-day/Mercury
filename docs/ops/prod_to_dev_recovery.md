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

## Restore-check account contract (v1)

The authoritative machine-readable contract is
`mercury.restore.account_contract`. Read-only status (no DCL or credentials in
output):

```bash
.venv/bin/python -m mercury.restore.account_contract
# During a separately approved exact-backup PI read window:
.venv/bin/python -m mercury.restore.account_contract --pi-read-window
```

Status is EXACT, MISSING, BROADER, or INSPECTION_FAILED. Exit zero means EXACT.
Repeat status is non-mutating. The ordinary managed-account artifact preflight
also refuses excess privileges, active roles, unrecognized grants and overlapping
unapproved schema rows. Missing *unrelated* development privileges in the account
inventory do not replace the artifact-specific capability check. Existing
disposable dev ALL grants remain approved; no global privileges or GRANT OPTION
are allowed. Unknown accounts do not inherit this named principal's contract.

The restore-check scope's explicit expected privileges are SELECT, INSERT,
CREATE, DROP, REFERENCES, INDEX, ALTER, LOCK TABLES, CREATE VIEW, SHOW VIEW,
TRIGGER, CREATE ROUTINE and ALTER ROUTINE. Extend the existing grant row rather
than creating a more-specific row that shadows it:

```sql
-- DBA REVIEW ONLY. Existing account; never applied automatically by Mercury.
GRANT CREATE ROUTINE, ALTER ROUTINE ON `_restorecheck\_%`.*
  TO 'mercury_dev_restore'@'localhost';
```

Routine privileges are database-pattern scoped, not global or production scoped.
MariaDB may automatically grant EXECUTE/ALTER ROUTINE on routines created in
disposable schemas, and can retain those grants after database cleanup. The
contract accepts only these two object-level routine privileges within the three
approved dev schemas or literal `_restorecheck_`-prefixed schemas. It rejects
such grants on production/PI schemas, extra privileges and grant option. It does
not claim those records prove the provenance of automatic creation. DBA review
may retire residual disposable routine grants; runtime does not mutate grants.

### Temporary production PI read window

Ordinary prod→dev sync rewrites PI to the dev clone and needs no production PI
SELECT. Production-shaped restore-checks preserve the original external schema
reference; their recreated views need SELECT on production PI. Use a temporary,
DBA-owned read window, not a permanent enlargement of the ordinary sync role:

```sql
GRANT SELECT ON `android_permission_intel`.* TO 'mercury_dev_restore'@'localhost';
-- Run account status --pi-read-window, exact artifact preflight and the approved
-- restore-check. Revoke after BOTH success and failure, or aborted execution:
REVOKE SELECT ON `android_permission_intel`.* FROM 'mercury_dev_restore'@'localhost';
```

No PI INSERT/UPDATE/DELETE/DDL/TRIGGER/EVENT/EXECUTE/routine/grant-option privileges.
Do not overlap ordinary sync with the temporary window: its managed-account
preflight conservatively refuses unexpected production read authority. The DBA
must ensure revocation on interruption and verify normal status afterward;
Mercury does not take an administrative credential or issue runtime GRANT/REVOKE.
After a failed rehearsal, preserve the target and receipts, but revoke PI SELECT
anyway. Dependent views may then become unreadable until a new approved diagnostic
read window; that does not authorize permanent production access.

Rollback of only the newly approved routine extension, if required:

```sql
REVOKE CREATE ROUTINE, ALTER ROUTINE ON `_restorecheck\_%`.*
  FROM 'mercury_dev_restore'@'localhost';
```

Recheck safe privilege metadata and status before every change. Do not replay a
revocation against permissions that were independently present before the change.
This SQL does not provision passwords or create accounts; the existing local
private-credential procedure remains unchanged.

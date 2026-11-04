# Noctivar preservation and isolated recovery

Mercury protects the six independent Noctivar repositories. `noctivar` is an
explicit backup-only production catalog; it has no approved dev sync pair.
Never restore over the live catalog. Venus remains primary research storage;
Mercury is the independent recovery destination.

## Scheduling and disk admission

The existing user timer runs repository sync daily at 20:00 local time with up
to five minutes of randomized delay. Its schedule and unit files are unchanged.
The scheduled Python entry point now read-triggers only the exact ext4
automount already configured in `/etc/fstab`, with a 20-second subprocess
deadline. User-manager services cannot directly order system-manager mount units.

After activation, the kernel must show the physical writable ext4 volume and
its source device must match the expected UUID. Autofs alone, a missing/wrong
disk, a read-only filesystem, destination symlinks and filesystem escapes refuse.
The existing Mercury free-space reserve is retained, with additional job
headroom: repository sync 10 GiB, protected database backup 2 GiB, selected
evidence its declared byte budget. These are admission checks, not reservations.
No disk options, UUIDs, fstab entries or retention settings are changed.

```sh
cd /home/systemadmin/GitHub/Mercury
.venv/bin/python -m mercury.cli backup health --json
systemctl --user status mercury-repo-sync.service mercury-repo-sync.timer
systemctl --user start mercury-repo-sync.service
```

Inspect health and active jobs before a manual start. Systemd serializes this
oneshot unit and the scheduled entry point also takes a nonblocking writer lock.
Never start a second copy through another route during that operation. A fresh
successful run clears the failed service result; do not reset failure state as a
substitute for creating a verified backup. Cold-boot physical activation remains
an operator observation to collect; automated tests simulate the unavailable states
without unmounting or disconnecting the real disk.

## Repository generations and worktree preservation

`config/repos.toml` registers all six Noctivar paths. The discovery defaults also
include them, so regenerating workstation configuration no longer omits the
database repository. The directory `noctivar-cli-utls` is intentional.

Mirrors copy all Git refs and detached HEAD without hardlinks and pass Git fsck.
Every run has a unique retained generation under
`/mnt/MERCURY_DATA/mercury_repo_clones/generations/`. A generation has STATE and
machine-readable receipts. Partial/failed runs do not replace the successful
`offline_repo_sync_receipt.json` pointer. New generation content is fsynced
before pointer publication. Prior generations are not deleted.

Version 2 worktree snapshots retain HEAD/branch, a binary-capable Git patch for
admitted text changes, safe untracked files, relative internal symlinks, modes,
per-file hashes and an outer manifest pin. Deleted tracked paths are recorded.
Source byte/identity changes refuse capture; relatime access-time changes do not.
They reconstruct HEAD-to-worktree content, not staging/index state.

Conservative filename/content filters exclude credentials and secret-like
content, ignored files, external links, special files, binaries and files over
8 MiB. Total admitted dirty content is capped at 256 MiB per repository. The
exclusion list is part of the snapshot manifest: a successful snapshot is not a
claim that every local file was preserved. An ignored file can still matter;
review it separately rather than weakening the filter. APKs, build outputs,
virtual environments and protected runtime credentials do not belong in a
source snapshot. Historical secrets already committed to Git are not scrubbed
by this process; mirror storage stays private.

Restore only into a new isolated directory with a nonsymlink parent:

```sh
.venv/bin/python -m mercury.repo.recovery \
  --receipt /mnt/MERCURY_DATA/mercury_repo_clones/offline_repo_sync_receipt.json \
  --key noctivar_noctivar_cli_database \
  --destination /home/systemadmin/Recovery/noctivar-database-review
```

Prepare the parent privately first. Choose a specific retained generation's
receipt when reproducibility matters. Recovery verifies mirror history,
snapshot/manifest bindings, selected file hashes, modes and symlinks. It does
not execute restored applications, fetch remotes or overwrite a source checkout.
V2 pins are required for dirty snapshot recovery; old snapshots retain their
historical format and are not silently upgraded.

## Database backup and its current privilege boundary

The managed catalog now recognizes `noctivar` as an exact production exception;
similarly named schemas remain unknown or governed by their existing rules.
Its live source schema, records, migration history and grants are unchanged.

The protected Linux lane reuses Mercury's MariaDB dump engine, content
verification, manifests and ledger. It serializes through a nonblocking lock,
stages outside managed successful backups, retains failed/interrupted bytes,
and publishes a new generation only after hash verification and durability
barriers. Source table DDL and all typed rows are fingerprinted in read-only
consistent transactions before and after the dump. Source changes prevent
semantic qualification. The dump uses `--single-transaction`; the no-data
companion skips unnecessary table locks. Concurrent DDL is outside its contract.
This lane is bounded to InnoDB base tables with primary keys, at most one million
rows and 2 GiB of serialized fingerprint input. Other schemas require their own
qualification; this is not an automatic replacement for every legacy backup route.

The existing generic Mercury account cannot SELECT Noctivar. Both existing
Noctivar accounts were also refused by the complete dump's SHOW EVENTS step.
Their grants were not changed or bypassed. Complete managed backup is therefore
still blocked pending an operator-reviewed backup principal with the necessary
table/object visibility and dump permissions. Review routine/trigger/event
requirements on the installed MariaDB version; do not infer the complete grant
set from one failed statement. Do not repurpose unrelated recovery credentials.

Once a suitable protected scoped socket profile exists:

```sh
.venv/bin/python -m mercury.cli backup run --db noctivar --kind full \
  --protected --credentials-json /protected/noctivar-backup-profile.json
```

The profile is an owner-only regular JSON file with exactly schema, socket,
user, password and role fields; the schema must match and socket must be
absolute. Credential contents are never placed in command arguments. Mercury's
existing private temporary client-defaults mechanism supplies them to the tool.

An explicit partial preservation mode is available:

```sh
.venv/bin/python -m mercury.cli backup run --db noctivar --kind full \
  --protected --tables-only --credentials-json /protected/scoped-profile.json
```

This excludes triggers, routines and events and prints that limitation. Its
manifest is marked `object_scope=tables_only`; it is published under
`mercury_research_backups/noctivar-table-snapshots`, outside normal managed
full backups, and is not recorded as complete production protection. Do not
rename or promote it to satisfy a full-backup requirement. Full dump failures
remain under `mercury_backup_incomplete` with failed/incomplete state.

## Isolated database recovery

```sh
.venv/bin/python -m mercury.restore.isolated_noctivar \
  --backup /mnt/MERCURY_DATA/mercury_research_backups/noctivar-table-snapshots/2026-11-04/noctivar/20261104_153505_919 \
  --receipt /home/systemadmin/Recovery/new-database-rehearsal.json
```

This command creates its own private `/tmp/mercury-noctivar-restore-*` datadir
and Unix socket, disables networking and LOCAL INFILE, imports into that instance,
and compares every selected table's DDL and ordered, typed row hashes with the
source fingerprint. It checks foreign keys and migration history. There is no
option to point it at the production server. The server is stopped afterward;
datadir and logs are retained for review rather than automatically removed.
Ensure at least 1 GiB temporary capacity for this observed catalog. Successful
table restoration does not validate excluded object classes, application startup,
provider secrets, the full APK population or workstation disaster recovery.

## Essential evidence protection

Selections are explicit hash-bound plans with source paths, archive-relative
names, a scope and a maximum byte budget. APK extensions, symlink source paths,
sensitive filenames/content and changed source bytes refuse admission. No
directory-wide corpus replication is implied. Generation files remain private.

```sh
.venv/bin/python -m mercury.backup.evidence backup \
  --plan /home/systemadmin/.local/state/mercury/noctivar-phase1-20261104T151915Z/evidence-protection-plan.json \
  --destination /mnt/MERCURY_DATA/mercury_research_backups/noctivar/generations/NEW_REVIEWED_GENERATION

.venv/bin/python -m mercury.backup.evidence restore \
  --generation /mnt/MERCURY_DATA/mercury_research_backups/noctivar/generations/noctivar-phase1-20261104T151915Z \
  --destination /home/systemadmin/Recovery/new-selected-evidence \
  --manifest-sha256 3584fdc8574955eecbd24fb46b833b523ad5025ca28b869be5027071ee4e7d81
```

Do not replay a frozen plan after source updates without preparing a distinct
selection. Existing generations refuse overwrite. Recovery validates inventory,
hashes and modes and refuses an existing destination. Bundle manifests retain
original source locators; restoration does not silently rewrite scientific packets.

The November 4 selections cover critical catalogs, plans, checkpoints, audits,
ingestion acknowledgements, source-alias/import provenance, operational ledgers,
three representative attempt packets and one frozen analyzer. They do not cover
all analysis outputs, the full SDK/JDK/OS, credentials/master keys, all 9,281
Venus APKs or the 816 home-only APKs. Legacy Mercury source copies overlap the
collection but do not establish complete current recovery coverage. The separate
PROTECTION registry records these gaps, destinations and recommended frequencies.

Repository snapshots should run daily and before milestones; catalog backups
should follow significant ingestion and a reviewed daily policy; metadata should
follow closed checkpoints. No new database/evidence timer is installed here.

## Status, rollback and remaining qualification

`mercury backup health --json` observes mount/device identity without activating
an absent mount and distinguishes the current service/lock state from historical
repository, database, snapshot and rehearsal receipts. It reports missing/stale
full database backups, incomplete generations, missing repository coverage and
research protection limits. It does not rehash entire archives during status.
The existing default backup commands remain available; the new protected lane's
locking and publication guarantees must not be attributed to every legacy route.

Rollback is a local revert of the focused Phase 1 Mercury commits, plus restoring
only the two reviewed local inventory files from the private baseline packet if
the operator intentionally withdraws coverage. Do not delete any new or historical
generation. Production unit/timer files and schedules need no rollback because
they were not changed. Rolling back source restores the old automount-only refusal
behavior. Keep existing dirty work separate from these commits and never use a
hard reset or blanket checkout.

Phase 1 remains partial until complete database-object backup/restore is verified.
Independent protection of home-only payloads and the whole research collection,
full application/workstation recovery, and a fresh cold-boot observation remain
separate qualifications. No provider request or analysis dispatch follows from
these backup and recovery commands.

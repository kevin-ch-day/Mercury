# Post-cutover storage evidence

After an approved HDD writer cutover, Mercury has three different package
identities. They must not be compared as though the USB remains the writer.

- **Final USB archive generation** is the durable package fingerprint recorded
  immediately before cutover. It is historical recovery evidence.
- **Cutover verified HDD generation** proves that final USB package was present
  on the HDD before the writer changed.
- **Active HDD package generation** is calculated from current durable
  HDD-managed artifacts. New backups, bundles, manifests, runbooks, snapshots,
  and restore-check evidence change it. Logs and transient state do not.

`./run.sh migration package-status` uses the HDD generation after
`migration_state=cutover_complete`; normal HDD changes are not USB/HDD drift.

## Legacy USB — fully phased out of normal operation

The USB (`MERCURY_DATA_USB`, UUID `e4f0c7fb-132e-4867-9c16-5e4749f5c43a`) is a
**retired offline recovery archive**. It is **not** a normal Mercury dependency.

After governed retirement (`LEGACY_USB_RETIRED_ARCHIVE_ONLY`):

| Concern | Behavior |
|---------|----------|
| Active writer | `MERCURY_DATA_V2` only |
| Backup / restore / migration readiness | Do not require USB |
| Doctor / dashboard / env check | No USB activation, mount, or writer repair |
| USB absent | No warning, failure, or degraded status |
| USB present | Informational archive detail only |
| Writable USB mount | Warns; never becomes a writer |

Record and inspect archive evidence with explicit commands only:

```bash
./run.sh storage archive-retire --confirm 'RETIRE LEGACY USB'
./run.sh storage archive-status
./run.sh storage archive-receipt
./run.sh storage archive-remount-ro
```

`archive-retire` is idempotent: it verifies primary health, cutover completion,
zero-mismatch generation evidence, and the retirement receipt; applies
`legacy_runtime_dependency = "none"` in local runtime policy; and never modifies
USB contents or mounts.

Physical USB removal, power-off, or later wipe/reuse remain operator decisions.
Mercury does not reactivate USB as a writer from normal workflows.

## Dirty worktrees

Git bundles alone do not contain uncommitted or untracked work. Mercury captures
dirty configured repositories with binary-capable staged/unstaged patches,
untracked non-ignored archive, ignored-file inventory, redacted remotes, history
bundle, fingerprint, and restore validation:

```bash
./run.sh migration capture-worktrees
./run.sh migration capture-worktrees --execute
./run.sh migration capture-worktrees --repo mercury --execute
```

Ignored files and runtime secret contents are never automatically copied. Review
runtime services, environment files, Apache/PHP/SELinux configuration, and host
packages separately before validating the destination workstation.

## Safe removal criteria

Retain the retired USB offline until the archive receipt and retirement receipt
are recorded, the HDD is readable on the destination, the active HDD package has
a fresh backup of the required seven schemas, and recovery exercises are
accepted. Mercury does not perform physical retirement, USB formatting, or
automatic rollback.

Future rollback must use a config lock and journal, validate all five writer
paths plus role/state, restore the saved configuration if validation fails, and
create an immutable audit record after explicit confirmation.

## Required seven-database backup scope

This research platform’s governed recovery set is **seven schemas**, not
production-only:

| Role | Databases |
|------|-----------|
| Production / shared authority | `android_permission_intel`, `erebus_threat_intel_prod`, `scytaledroid_core_prod`, `obsidiandroid_core_prod` |
| Required development | `android_permission_intel_dev`, `erebus_threat_intel_dev`, `scytaledroid_core_dev` |

The three development schemas are **mandatory** for workstation recovery and
tester readiness on this platform. They are confirmation-gated so they cannot be
confused with accidental `*_dev` discovery noise, but they are not optional
afterthoughts for this environment.

Preferred governed lane (prod + required dev in one full-backup run):

```bash
./run.sh backup full --include-dev --confirm-dev 'BACKUP DEV DATABASES'
```

Development-only lane (same confirmation phrase):

```bash
./run.sh backup dev
./run.sh backup dev --execute --confirm 'BACKUP DEV DATABASES'
```

Re-verify an already-written development backup without creating another dump
(exact `--backup-id` preferred; `--allow-development-recovery` required):

```bash
./run.sh backup verify --db android_permission_intel_dev --allow-development-recovery
./run.sh backup verify --db erebus_threat_intel_dev --allow-development-recovery
./run.sh backup verify --db scytaledroid_core_dev --allow-development-recovery
```

Destination import of development backups remains confirmation-gated and accepts
only configured development targets:

```bash
./run.sh deploy dev --dry-run
./run.sh deploy dev --execute --confirm 'DEPLOY DEV BACKUPS'
```

Development artifacts remain outside the default **sealed destination handoff
package** membership rules unless policy explicitly includes them. That packaging
boundary does **not** make the three development schemas optional for local
Fedora recovery, restore-check drills, or Asus tester readiness—those workflows
require all seven.

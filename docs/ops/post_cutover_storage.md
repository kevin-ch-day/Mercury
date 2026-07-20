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
`migration_state=cutover_complete`; it does not call normal HDD changes USB/HDD
drift.

## USB recovery archive

The USB remains an **application-policy archive** after cutover. Mercury blocks
normal writes to it, but this is not the same as an operating-system read-only
mount. Check the actual filesystem mount mode and record immutable archive
evidence with:

```bash
./run.sh storage archive-receipt
./run.sh storage archive-receipt --execute
```

The receipt is written on the HDD under `.mercury_control/`, never onto USB. It
contains the USB identity, durable relative-path manifest and SHA-256, generation
and mount mode. It is write-once unless an explicit administrative override is
used. Physical USB removal or erasure remains an operator decision; it is not
authorized by recording a receipt.

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

Retain the USB until the archive receipt is recorded, the HDD is readable on the
destination, the active HDD package has a fresh backup, and recovery exercises
are accepted. Mercury does not perform physical retirement, USB formatting, or
automatic rollback.

Future rollback must use a config lock and journal, validate all five writer
paths plus role/state, restore the saved configuration if validation fails, and
create an immutable audit record after explicit confirmation.

## Optional development recovery backups

Routine protection remains production and shared-authority only.  When a
workstation move needs existing development schemas/data as a fallback, use the
separate, confirmation-gated lane for configured development targets:

```bash
./run.sh backup dev
./run.sh backup dev --execute --confirm 'BACKUP DEV DATABASES'
```

To re-verify an already-written optional development recovery backup without
creating another dump, use the explicit recovery gate. It accepts only the
configured recovery databases and stamps `manifest.json` only after the normal
artifact checks pass:

```bash
./run.sh backup verify --db android_permission_intel_dev --allow-development-recovery
./run.sh backup verify --db erebus_threat_intel_dev --allow-development-recovery
./run.sh backup verify --db scytaledroid_core_dev --allow-development-recovery
```

They can be planned on the receiving PC without weakening the normal
production deployment lane:

```bash
./run.sh deploy dev --dry-run
./run.sh deploy dev --execute --confirm 'DEPLOY DEV BACKUPS'
```

The live import is confirmation-gated and accepts only configured development
targets. These artifacts remain optional recovery material; they do not change
the production-protection status or make development databases part of the
routine handoff package.

# Destination package create and safe HDD disconnect

## Package create (fail-closed)

```bash
./run.sh migration package seal \
  --run-id 20260722T055400Z_phase3b \
  --mercury-commit <full-sha> \
  --mercury-capture-id <capture-id>

./run.sh migration package create \
  --preview-id <exact-preview-id> \
  --run-id 20260722T055400Z_phase3b \
  --mercury-commit <full-sha> \
  --mercury-capture-id <capture-id> \
  --confirm 'CREATE DESTINATION PACKAGE'
```

## Safe disconnect (guided menu)

Prefer the operator menu (no manual `fuser`/`lsof`/`systemctl` assembly):

```text
Main Menu → Mercury HDD and Storage → Safe disconnect Mercury HDD
  (recommended primary action when ready; or Reconnect or change storage mode)
```

Or CLI:

```bash
./run.sh storage detach status
./run.sh storage detach wizard
./run.sh storage detach execute --confirm 'DETACH MERCURY HDD'
```

Identity is always resolved by filesystem UUID
`715f29a9-2671-477b-8c8d-515d190addb9` (label `MERCURY_DATA_V2`, model `WDC WD10JDRW`).
Never trust a fixed `/dev/sdX` letter.

Mercury may invoke interactive `sudo` so the terminal can prompt for a password.
Mercury never reads, stores, echoes, or logs that password.

Reconnect / validate:

```bash
./run.sh storage reconnect --mode source
./run.sh storage reconnect --mode destination --read-only --mount
```

Writes stay disabled until `--restore-writes 'RESTORE MERCURY WRITES'`.

## Detach / write-disabled maintenance

While host maintenance has `writes_allowed=false` (detaching or detached):

* Backup Operations refuses full/prod/dev backup, verify-with-stamp, restore-check, and bundle write **before** prompts.
* Handoff Tools write choices refuse before guided wizard / phase prompts.
* Operator-storage writers share a central gate (`assert_operator_storage_path` + host maintenance).
* No governed HDD receipts are written under `.mercury_control/full_backup_runs/`.
* Optional host-local refusal audits go to `~/.local/share/mercury/refused_operations/` and are never handoff evidence.
* Logs redirect off the Mercury HDD to `~/.local/share/mercury/detach_logs/`.
* Live migrate-run, cleanup plan writes under the primary mount, migration progress ledger, destination document generation, quarantine execute, generation receipts, and deploy report writers also refuse.

Inspect invalid maintenance-mode full-backup receipts (observe-only):

```bash
./run.sh backup full-receipts plan
```

Known invalid example from a pre-fix refusal:

```text
.mercury_control/full_backup_runs/20260722T211549Z_full_backup.json
```

Classify as `invalid_maintenance_mode_artifact`. Quarantine later under
`.mercury_control/quarantine/invalid_maintenance_receipts/` (keep `.sha256`; do not delete).
Do not treat it as a successful backup run or destination-package member.

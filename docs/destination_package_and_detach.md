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
Doctor → Open storage migration menu
  → [3] Safe disconnect Mercury HDD
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

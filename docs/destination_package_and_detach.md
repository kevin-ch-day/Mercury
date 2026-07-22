# Destination package create and HDD detach

## Package create (fail-closed)

Seal a governed preview (exact membership + fingerprints + SHA-256), then create:

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

Create requires an exact sealed `--preview-id` (never run-id alone, never unqualified `latest`).
Packages land under:

`/mnt/MERCURY_DATA_V2/.mercury_control/destination_packages/<package-id>/`

Successful verification status must be `DESTINATION_PACKAGE_VERIFIED`.

## Safe detach (stops before privileged unmount)

```bash
./run.sh storage detach status
./run.sh storage detach preview
./run.sh storage detach execute --confirm 'DETACH MERCURY HDD'
```

`execute` marks host-local write-disable (`~/.local/share/mercury/host_maintenance.json`) and prints operator sudo commands. It does **not** run `systemctl stop`, `umount`, or `udisksctl power-off`.

Before detach: exit Mercury menu (`[0] Exit`), then confirm no holders with `sudo fuser` / `sudo lsof`.

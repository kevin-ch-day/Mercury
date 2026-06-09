# Production to development sync policy

## Overview

For the current Fedora milestone, Mercury plans sync only for:

- `erebus_threat_intel_prod` -> `erebus_threat_intel_dev`
- `scytaledroid_core_prod` -> `scytaledroid_core_dev`

Dev databases are **not** backup sources. For this milestone they are disposable refresh targets and may be rebuilt or overwritten during sync.

The reason Mercury requires verified source backups before sync is to protect the source state before using it to refresh dev, not because the `_dev` databases contain preservation-worthy data.

## Required order of operations

1. **Classify** — Confirm prod and dev pair names and roles.
2. **Backup source** — Full backup of the `*_prod` source before any dev refresh.
3. **Verify backup** — Confirm backup integrity before any sync.
4. **Sync to dev** — Restore verified backup into `*_dev` only after steps 2–3 succeed.

## Prohibitions

- Never drop or overwrite `*_prod` as part of a dev sync.
- Never skip source backup/verify before syncing into dev.
- Never treat `*_dev` as a backup source or preservation target for this milestone.

## Execution gates

Sync and restore-check are **implemented** but gated by default:

- `[mercury] dry_run = false` and `live_actions_enabled = true` in `config/local.toml`, or
- `MERCURY_DRY_RUN=0` and `MERCURY_LIVE_ACTIONS=1` in the environment.

For the first live milestone, backup execution must also target the mounted USB-backed root under `/mnt/MERCURY_DATA_USB/mercury_backups`. Repo-local `backups/` do not count as production protection or sync readiness in live/operator mode.

CLI:

```bash
mercury sync readiness --live
mercury sync run --live --execute         # requires typing SYNC DEV
```

Menu: **Sync Production -> Development** → Prepare → Sync ready pairs (requires typing `SYNC DEV`).

Out-of-scope databases (for example `gecko_research_database_*`, `droid_threat_intel_db_*`, and `proofpoint_cti_db_dev`) do not participate in sync planning for this milestone. Live discovery may still show them for operator awareness, but they are not treated as backup or sync blockers.

# Production to development sync policy

## Overview

For the current Fedora milestone, Mercury plans sync only for:

- `erebus_threat_intel_prod` -> `erebus_threat_intel_dev`
- `scytaledroid_core_prod` -> `scytaledroid_core_dev`

Dev databases are **not** backup sources and may be rebuilt or overwritten during sync.

## Required order of operations

1. **Classify** — Confirm prod and dev pair names and roles.
2. **Backup production** — Full backup of the `*_prod` source.
3. **Verify backup** — Confirm backup integrity before any sync.
4. **Sync to dev** — Restore verified backup into `*_dev` only after steps 2–3 succeed.

## Prohibitions

- Never drop or overwrite `*_prod` as part of a dev sync.
- Never skip production backup/verify before syncing into dev.
- Never treat `*_dev` as a backup source.

## Execution gates

Sync and restore-check are **implemented** but gated by default:

- `[mercury] dry_run = false` and `live_actions_enabled = true` in `config/local.toml`, or
- `MERCURY_DRY_RUN=0` and `MERCURY_LIVE_ACTIONS=1` in the environment.

CLI:

```bash
mercury sync readiness --live
mercury sync run --live --execute --yes   # skips SYNC DEV prompt only with --yes
```

Menu: **Sync Production -> Development** → Prepare → Sync ready pairs (requires typing `SYNC DEV`).

Out-of-scope databases (for example `gecko_research_database_*`, `droid_threat_intel_db_*`, and `proofpoint_cti_db_dev`) do not participate in sync planning for this milestone. Live discovery may still show them for operator awareness.

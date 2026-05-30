# Production to development sync policy

## Overview

Development databases (`*_dev`) receive data from their production counterparts (`*_prod`). Dev databases are **not** backup sources and may be rebuilt or overwritten during sync.

## Required order of operations

1. **Classify** — Confirm prod and dev pair names and roles.
2. **Backup production** — Full backup of the `*_prod` source.
3. **Verify backup** — Confirm backup integrity before any sync.
4. **Sync to dev** — Copy or restore into `*_dev` only after steps 2–3 succeed.

## Prohibitions

- Never drop or overwrite `*_prod` as part of a dev sync.
- Never skip production backup/verify before syncing into dev.
- Never treat `*_dev` as a backup source.

## Seed mode

Prod-to-dev sync is **not implemented** in seed. Menu and CLI entries are placeholders only.

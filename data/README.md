# Mercury Local State

This directory is the repo-local development fallback for Mercury's portable
operation ledger.

## Operator / production ledger

When the **active write root** is mounted (today: transitional USB until cutover),
Mercury prefers:

```text
{active_mount}/mercury_state/operations.jsonl
{active_mount}/mercury_state/database_backups.csv
{active_mount}/mercury_state/repo_bundles.csv
{active_mount}/mercury_state/database_bundles.csv
{active_mount}/mercury_state/transfer_packages.csv
{active_mount}/mercury_state/sync_events.csv
```

Typical mounts:

- Transitional / legacy: `/mnt/MERCURY_DATA_USB/mercury_state/`
- Canonical primary (after cutover): `/mnt/MERCURY_DATA_V2/mercury_state/`

Inspect roles with `./run.sh storage status`.

## Repo-local fallback

Mercury falls back to this repo-local `data/` directory only when the active
operator mount is not available. That path is for development and tests — it
does not count as production protection.

Do not store secrets here.

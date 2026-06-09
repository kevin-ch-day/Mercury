# Mercury Local State

This directory is the repo-local development fallback for Mercury's portable
operation ledger.

Production/operator runs should prefer the USB-backed ledger at:

- `/mnt/MERCURY_DATA_USB/mercury_state/operations.jsonl`
- `/mnt/MERCURY_DATA_USB/mercury_state/database_backups.csv`
- `/mnt/MERCURY_DATA_USB/mercury_state/repo_bundles.csv`
- `/mnt/MERCURY_DATA_USB/mercury_state/transfer_packages.csv`
- `/mnt/MERCURY_DATA_USB/mercury_state/sync_events.csv`

Mercury falls back to this repo-local `data/` directory only when the required
USB mount is not active.

Do not store secrets here.

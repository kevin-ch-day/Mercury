# Mercury v1 checklist

Mercury v1 closeout is complete for the scoped transfer lanes below.

## Recorded v1 closeout

- Database package: complete
- Repository package: complete with warnings
- Transfer package: complete
- Prod-to-dev sync: executed
- Dirty repo warnings: recorded truthfully at bundle time
- Recovery deployment: deploy verified operator-storage DB/repo artifacts onto a prepared Fedora host
- Full workstation provisioning: out of scope for Mercury

## Database lane

- Source databases backed up to `/mnt/MERCURY_DATA_USB/mercury_backups`
- Full backups verified with manifest, checksum, size, and role checks
- Restore-check completed successfully for each active source database
- Latest verified backups present on the USB for each active source database
- Production sync readiness clearly reported for the two active prod→dev pairs
- Actual prod→dev sync executed after readiness passed and `SYNC DEV` was confirmed

## Repository lane

- Configured repositories discovered from `config/repos.toml`
- Repo path, branch, commit, remote, clean/dirty state, untracked count, and upstream status reported
- Git bundles written to `/mnt/MERCURY_DATA_USB/mercury_repo_backups/2026-06-09`
- Git bundle verification recorded (bundle exists, size > 0, `git bundle verify` passed)
- Repo manifests written to `/mnt/MERCURY_DATA_USB/mercury_manifests/2026-06-09`
- Short restore/import notes written to `/mnt/MERCURY_DATA_USB/mercury_runbooks/2026-06-09`
- Dirty repo warnings captured truthfully when bundles were created from dirty worktrees

Recorded warnings:

- Dirty repos at bundle time included `Mercury` and `Linux Scripts`
- Git bundles include committed history only; uncommitted local changes are not part of the bundle
- `ScytaleDroid` was behind upstream at bundle time

## Transfer artifacts

- Database backup manifests available on the USB
- Repository manifests available on the USB
- Combined transfer manifest written to `/mnt/MERCURY_DATA_USB/mercury_manifests/transfer_manifest_20260609_031800.json`
- Combined transfer runbook written to `/mnt/MERCURY_DATA_USB/mercury_runbooks/transfer_runbook_20260609_031800.md`
- Short restore/import instructions available for both database and repository transfers
- USB paths verified for database, repository, manifest, and runbook outputs
- Mercury portable state written under `/mnt/MERCURY_DATA_USB/mercury_state`

## v1 completion summary

- Database package: complete
- Repository package: complete with warnings
- Transfer package: complete
- Sync decision recorded as executed

## Out of scope

Mercury v1 does not include Fedora package installation, systemd/service setup, Apache/httpd, SELinux policy work, Linux user/group management, MariaDB user/grant management, or full workstation bootstrap.

Recovery deployment (`mercury deploy …`) **is** in scope: importing verified operator-storage database backups and configured repository bundles onto a host that is already prepared for Mercury operations.

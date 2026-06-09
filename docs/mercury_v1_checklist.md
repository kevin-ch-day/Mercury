# Mercury v1 checklist

Mercury v1 is complete when the scoped transfer lanes below are complete.

## Database lane

- Source databases backed up to `/mnt/MERCURY_DATA_USB/mercury_backups`
- Full backups verified with manifest, checksum, size, and role checks
- Restore-check completed successfully for each active source database
- Production sync readiness clearly reported for the two active prod→dev pairs
- Optional prod→dev sync executed only after readiness passes and verified afterward

## Repository lane

- Configured repositories discovered from `config/repos.toml`
- Repo path, branch, commit, remote, clean/dirty state, untracked count, and upstream status reported
- Git bundles written to `/mnt/MERCURY_DATA_USB/mercury_repo_backups`
- Repo manifests written to `/mnt/MERCURY_DATA_USB/mercury_manifests`
- Short restore/import notes written to `/mnt/MERCURY_DATA_USB/mercury_runbooks`

## Transfer artifacts

- Database backup manifests available on the USB
- Repository manifests available on the USB
- Short restore/import instructions available for both database and repository transfers

## Out of scope

Mercury v1 does not include Fedora package installation, systemd/service setup, Apache/httpd, SELinux policy work, Linux user/group management, MariaDB user/grant management, or full workstation bootstrap.

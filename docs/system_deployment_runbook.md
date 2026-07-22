# System recovery deployment runbook (Neptune / prepared Fedora host)

Mercury can restore **Mercury-managed artifacts** — verified operator-storage database backups and configured Git repositories — onto a **prepared Fedora/MariaDB host**. This is recovery deployment, not full workstation provisioning.

Mercury does **not** install Fedora packages, configure systemd, manage MariaDB users/grants, or bootstrap a bare OS. The host must already have MariaDB, Git, and operator paths ready (see `mercury env doctor`).

## Lanes

| Lane | Command | Source |
|------|---------|--------|
| Databases | `./run.sh deploy db --dry-run` | Verified operator-storage SQL backups |
| Repositories (GitHub) | `./run.sh deploy repos --from-github --dry-run` | `remote_url` in `config/repos.toml` |
| Repositories (operator storage) | `./run.sh deploy repos --from-usb --dry-run` | Archived bundle compatibility flag; reads configured operator-storage bundles + manifests |
| Combined plan | `./run.sh deploy system --dry-run` | Both |

Menu: **option 8 → Deploy to this system**

## Fresh host recovery workflow

```bash
cd ~/GitHub/Mercury
./run.sh doctor
./run.sh repo init-config          # writes config/repos.toml for ~/GitHub/*
# Edit config/repos.toml — add remote_url for each repo you want from GitHub

./run.sh deploy system --dry-run   # full plan
./run.sh deploy db --dry-run       # databases only
./run.sh deploy repos --dry-run    # repos only (auto: GitHub if remote_url, else USB bundle)
```

## Repository config

`config/repos.toml` fields:

- `path` — target checkout directory on this machine
- `remote_url` — GitHub/Git remote for `./run.sh deploy repos --from-github`
- `default_branch` — branch passed to `git clone --branch`

Example:

```toml
[repos.mercury]
display_name = "Mercury"
path = "/home/linuxadmin/GitHub/Mercury"
default_branch = "main"
remote_url = "https://github.com/your-org/Mercury.git"
```

Bundle manifests live under the configured operator-storage `mercury_manifests/*/` tree (the canonical HDD after cutover). Mercury picks the newest `.repo_manifest.json` per repo key.

## Safety

- Dry-run is default; live clone requires `live_actions_enabled=true` and `--execute` or menu y/n.
- Existing git checkouts are **skipped** by default (`--skip-existing`).
- Mercury never deletes, force-pushes, or overwrites existing repositories.

## Live execution

```toml
# config/local.toml
[mercury]
dry_run = false
live_actions_enabled = true
```

```bash
./run.sh deploy repos --from-github --execute
./run.sh deploy db --execute
```

## User cases covered

Run `./run.sh deploy use-cases` (or menu option 8 → 5) to see what applies on this host.

| Use case | When | Command |
|----------|------|---------|
| Fresh full rebuild | Missing DBs + repos, operator storage ready | `deploy system --dry-run` |
| Databases only | Verified operator-storage SQL backups, DBs missing | `deploy db --dry-run` |
| Repos from GitHub | `remote_url` or bundle manifest remote | `deploy repos --from-github --dry-run` |
| Repos from archived bundles | Offline bundle restore (`--from-usb` is compatibility naming) | `deploy repos --from-usb --dry-run` |
| Stale repos.toml | Paths still under `/home/secadmin/...` | `repo init-config --force` |
| Partial checkout | Some repos exist, others missing | `deploy repos --dry-run` |

During planning, Mercury rewrites stale paths to the current home directory automatically. Persist the fix with `repo init-config --force` (also merges `remote_url` from bundle manifests).

See also: [database_deployment_runbook.md](database_deployment_runbook.md)

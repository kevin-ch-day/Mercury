# Live MariaDB discovery and read-only access

Mercury connects to MariaDB using **`config/local.toml`** for read-only operations. No backups, restores, sync, or schema changes are performed by these commands.

## Commands

| Command | Purpose |
|---------|---------|
| `mercury db ping` | Connectivity probe (VERSION, CURRENT_USER, sample DBs) |
| `mercury db discover` | `SHOW DATABASES` + classification |
| `mercury db inspect --name <db>` | Table/view counts and size via `information_schema` |
| `mercury db access` | Platform catalog vs server presence |
| `mercury env probe --check-db` | Environment summary + optional probe |

Demo/offline mode (no server):

```bash
mercury db discover --demo
```

## Configuration

```bash
mercury config init
```

USB backup root for the first Mercury transfer/backup run:

```toml
[mercury]
backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"
log_dir = "/mnt/MERCURY_DATA_USB/mercury_logs"
dry_run = true
live_actions_enabled = false
```

Out-of-scope databases such as legacy `gecko_research_database_*` (Komodo/market-event naming), `droid_threat_intel_db_prod`, and `proofpoint_cti_db_dev` may still appear in live discovery output. Mercury keeps them visible for operator awareness, but excludes them from active backup/sync planning for this milestone.

### Option A — TCP + password (remote / password auth)

```toml
# config/local.toml
[mariadb]
host = "127.0.0.1"
port = 3306
user = "mercury_readonly"
password_env = "MERCURY_MARIADB_PASSWORD"
connect_timeout = 10
ssl_disabled = true
use_client = false
```

```bash
export MERCURY_MARIADB_PASSWORD='your-password'
mercury db ping
```

Requires **pymysql** (`pip install -e ".[mariadb]"`).

### Option B — Local Fedora unix socket (recommended for dev)

Uses `mariadb`/`mysql` CLI on PATH with socket auth (e.g. `root@localhost`):

```toml
[mariadb]
host = "127.0.0.1"
port = 3306
user = "root"
use_client = true
unix_socket = "/var/lib/mysql/mysql.sock"
connect_timeout = 10
ssl_disabled = true
```

Optional `password_env` when TCP fallback is needed.

If `./run.sh` cannot reach PyPI, keep using an existing synced virtualenv with
`MERCURY_SKIP_SYNC=1 ./run.sh`, or rebuild with Fedora packages plus
`python3 -m venv --system-site-packages .venv`.

## Access modes

| `use_client` | Driver | Module |
|--------------|--------|--------|
| `true` | mariadb/mysql CLI | `database/mariadb/client.py` |
| `false` | pymysql | `database/mariadb/session.py` |

On Fedora, root over pymysql often fails (unix_socket plugin); prefer Option B for local dev.

## System databases

Filtered out: `information_schema`, `mysql`, `performance_schema`, `sys`.

Discovered names are classified with the same rules as demo mode (`*_prod`, `*_dev`, `android_permission_intel`, etc.).

## SQL executed (read-only)

- `SELECT 1`
- `SELECT VERSION()`
- `SELECT CURRENT_USER() AS mercury_current_user`
- `SHOW DATABASES`
- `information_schema` queries for inspect

No DDL or DML.

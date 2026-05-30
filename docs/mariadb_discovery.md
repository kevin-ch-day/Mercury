# Live MariaDB discovery (M5)

`mercury db discover` (without `--demo`) connects to MariaDB using **`config/local.toml`** and runs a single read-only statement:

```sql
SHOW DATABASES;
```

No `CREATE`, `DROP`, `ALTER`, backups, restores, or sync operations are performed.

## Configuration

Copy and edit local config:

```bash
mercury config init
```

`config/local.toml`:

```toml
[mariadb]
host = "127.0.0.1"
port = 3306
user = "mercury_readonly"
password_env = "MERCURY_MARIADB_PASSWORD"
connect_timeout = 10
ssl_disabled = true
```

## Password via environment

```bash
export MERCURY_MARIADB_PASSWORD='your-readonly-password'
mercury db discover
```

Mercury fails clearly if `config/local.toml` is missing, `[mariadb]` is incomplete, or the password env var is unset.

## Install driver

```bash
pip install -e ".[mariadb,dev]"
```

Requires **pymysql** for live discovery.

## Demo mode (unchanged)

```bash
mercury db discover --demo
```

Uses platform catalog + `databases.toml` — no server connection.

## System databases

Filtered out: `information_schema`, `mysql`, `performance_schema`, `sys`.

Discovered names are classified with the same rules as demo mode (`*_prod`, `*_dev`, `android_permission_intel`, etc.).

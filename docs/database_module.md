# Database module (`mercury.database`)

Discovery, MariaDB connectivity, dry-run planning, policy, and CLI sit on top of **`mercury.database.core`**, which owns models, catalog, classification, config loading, and inventory only.

## Layout

```
mercury/database/
  __init__.py          Public API
  core/                Core only — no discovery, MariaDB, planning, CLI
    models.py          DatabaseRecord, DatabaseInventory
    catalog.py         Platform reference catalog
    classifier.py      *_prod / *_dev / shared authority rules
    config_files.py    databases.toml loaders
    inventory.py       Build records + role summaries
    inventory_ops.py   Backup sources, grouping, entry formatting
    sources.py         Provenance labels (config, catalog, live)
  facade.py            DatabaseService facade
  commands.py          Commands for `mercury db` and `mercury database`
  terminal/            CLI output helpers (inventory, inspect, ping, policy, …)
  backup_planning.py   Dry-run backup plans
  policy.py            Config/catalog policy validation
  prod_dev_pairs.py    Prod → dev pair inference
  discovery/
    __init__.py        discover(mode="demo"|"config"|"live")
    config.py          Config + catalog (offline)
    demo.py            Demo / seed discovery
  mariadb/
    config.py          local.toml [mariadb] + password_env
    live.py            SHOW DATABASES (read-only)
    probe.py           Client tooling on PATH
```

Legacy shims (`service.py`, `cli.py`, `*_terminal.py` at package root, and top-level `src/mercury/*.py` compatibility modules) re-export from the canonical paths above. New code should import the subpackages directly.

## Core API

```python
from mercury.database.core import (
    classify_database,
    DatabaseInventory,
    PLATFORM_DATABASES,
    record_from_name,
    backup_source_names,
)

c = classify_database("erebus_threat_intel_prod")
assert c.backup_source
```

## Discovery

```python
from mercury.database import discover, discover_from_config, discover_demo

inventory = discover("demo")              # catalog + config, no server
inventory = discover_from_config()        # config (+ optional catalog)
inventory = discover("live")              # SHOW DATABASES (needs config/local.toml)
```

CLI:

- `mercury db discover --demo` → `discover("demo")`
- `mercury db discover` → `discover("live")`
- `mercury database summary --demo` → short counts and backup sources

## Planning and policy

```python
from mercury.database.backup_planning import build_demo_backup_plan
from mercury.database.policy import validate_config_policy
from mercury.database import backup_source_names
```

## Service facade

```python
from mercury.database import default_service

inv = default_service.discover_demo()
plan = default_service.backup_plan_demo()
default_service.print_pairs(inv)
```

## Import map (non-core features)

| Need | Import from |
|------|-------------|
| Classify / catalog / inventory models | `mercury.database.core` |
| Discovery | `mercury.database` or `mercury.database.discovery` |
| MariaDB live | `mercury.database.mariadb` |
| Backup dry-run plans | `mercury.database.backup_planning` |
| Policy validation | `mercury.database.policy` |
| Prod→dev pairs | `mercury.database.prod_dev_pairs` |
| CLI output helpers | `mercury.database.terminal` |

Prefer `mercury.database` for the stable operator-facing API; use submodules when you want a narrow dependency boundary.

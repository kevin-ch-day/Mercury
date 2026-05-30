# Database backup policy

Mercury protects platform MariaDB databases for **Erebus**, **ScytaleDroid**, **ObsidianDroid** (gecko), and shared **`android_permission_intel`**.

## Full vs schema-only

| Type | Purpose | Backup source? |
|------|---------|----------------|
| **Full logical** | Schema + data — DR and prod-to-dev sync | Yes, for `*_prod` and shared authority |
| **Schema-only** | Structure only — review and empty DB shells | Same sources as full; **not** a substitute for full backups |

Schema-only uses planned `*.schema.sql.gz` files. Full verified backups are **required** before any prod-to-dev sync.

## What to back up

| Pattern / name | Role | Backup source |
|----------------|------|---------------|
| `*_prod` | Production | Yes |
| `android_permission_intel` | Shared authority | Yes |
| `*_dev` | Development | **No** — disposable sync targets |
| `_restorecheck_*` | Restore-check temp | **No** |
| Other | Unknown | **No** (manual review) |

## Rules

1. **Back up only production and designated shared authority databases.**
2. **Never back up `*_dev` databases** — rebuild from verified production backups.
3. **Never drop or overwrite `*_prod`.**
4. **Restore-check databases** are temporary; exclude from all backup plans.
5. **Unknown names** require manual review.

## Verification and protection

- A backup is **not protected** until verification passes (manifest + checksum + size + role checks).
- **Schema-only** verified backups are useful for schema review and empty shells; they are **not** sufficient for full DR or prod-to-dev sync.
- **Full verified** backups are required before prod-to-dev sync.

## Seed mode (M4 / M4.5)

- `mercury backup schema-plan --demo` — schema-only dry-run plan
- `mercury backup manifest-preview` — JSON preview only; no files written
- `mercury backup verify-plan --demo` — verification process model; no real file checks
- `mercury backup list --demo` — demo planned records only
- `mercury report preview --db <name> --kind full|schema_only` — Markdown report preview
- No `mariadb-dump` execution, no live connections, `live_actions = false`

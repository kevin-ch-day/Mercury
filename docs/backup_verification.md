# Backup verification

A database is **not considered protected** until backup verification passes.

## Required artifacts

Each backup run under `backups/YYYY-MM-DD/<database>/` should include:

- `manifest.json` — backup metadata
- `checksum.sha256` — integrity check for dump files
- Dump file(s): `.sql.gz` (full) and/or `.schema.sql.gz` (schema-only)
- `backup_report.md` — human-readable outcome

## Verification checks

Implemented in `mercury.backup.verification`:

1. `manifest.json` exists and parses
2. Dump/schema files exist per backup kind
3. `checksum.sha256` exists and matches artifacts
4. File sizes > 0
5. Database role is an approved backup source
6. Backup kind is `full` or `schema_only`
7. Policy fields recorded in manifest

## CLI

Verify latest on-disk backup for a database:

```bash
mercury backup verify --db erebus_threat_intel_prod
mercury backup verify --db erebus_threat_intel_prod --path /path/to/backup/dir
mercury backup verify --db erebus_threat_intel_prod --update-manifest
```

Preview/demo (no files on disk):

```bash
mercury backup verify-plan --demo
mercury backup list --demo
```

## Schema-only vs full

| Kind | Verification | DR / prod-to-dev |
|------|--------------|------------------|
| **schema_only** | Can pass for schema artifact | **Not sufficient** for full DR or prod-to-dev sync |
| **full** | Must pass before trust | Required before prod-to-dev sync (with policy gates) |

Full **verified** backups are required before any prod→dev sync.

## After a live backup

```bash
mercury backup run --db erebus_threat_intel_prod --kind full --execute
mercury backup verify --db erebus_threat_intel_prod --update-manifest
```

Live execution requires `[mercury] dry_run = false` and `live_actions_enabled = true` in `config/local.toml`.

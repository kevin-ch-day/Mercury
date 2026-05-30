# Backup layout (planned)

Mercury uses a single layout helper (`mercury.backup.layout`) for all dry-run planning:

```
backups/YYYY-MM-DD/<database>/
  <database>_<timestamp>.sql.gz           # full logical (schema + data)
  <database>_<timestamp>.schema.sql.gz    # schema-only (structure only)
  manifest.json
  checksum.sha256
  backup_report.md
```

On Fedora the backup root may be configured (e.g. `/var/backups/mercury`); paths above are relative to that root.

## Backup kinds

| Kind | File | Contents |
|------|------|----------|
| **full** | `.sql.gz` | Schema + data — disaster recovery and prod-to-dev sync |
| **schema_only** | `.schema.sql.gz` | Tables/views/routines/triggers/events only — no table data |

Schema-only backups support schema review and rebuilding **empty** database shells. They do **not** replace full backups.

## manifest.json (future / preview)

Production manifests will include at least:

- `backup_id`, `database`, `backup_kind`, `created_at`, `dump_file`, `sha256`, `size_bytes`
- `source_role`, `tool_used`, `verified`, `notes`

**Manifest preview** (seed, no file write):

```bash
mercury backup manifest-preview --db erebus_threat_intel_prod --kind schema_only
mercury backup manifest-preview --db erebus_threat_intel_prod --kind full
```

Preview adds planning fields: `planned_directory`, `planned_dump_file`, `planned_schema_file`, `dry_run`, `live_actions_enabled`, `tool_family`, `project`, `role`.

## Verification

After backups exist on disk, Mercury will read `manifest.json` and `checksum.sha256`, confirm dump files exist with non-zero size, and set `verified: true` in the manifest. Until then the database is not considered protected. See [backup_verification.md](backup_verification.md).

## Commands using this layout

- `mercury backup plan --demo`
- `mercury backup schema-plan --demo`
- `mercury backup manifest-preview --db <name> --kind full|schema_only`
- `mercury backup verify-plan --demo`
- `mercury backup list --demo`
- `mercury report preview --db <name> --kind full|schema_only`

## Implementation model

MariaDB [mariadb-dump](https://mariadb.com/docs/server/clients-and-utilities/backup-restore-and-import-utilities/mariadb-dump) performs logical backups. Schema-only planning maps to `--no-data` style options. **Seed mode does not run dumps or connect to databases.**

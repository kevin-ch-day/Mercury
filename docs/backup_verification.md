# Backup verification (planned)

A database is **not considered protected** until backup verification passes.

## Required artifacts

Each backup run under `backups/YYYY-MM-DD/<database>/` should include:

- `manifest.json` — backup metadata
- `checksum.sha256` — integrity check for dump files
- Dump file(s): `.sql.gz` (full) and/or `.schema.sql.gz` (schema-only)
- `backup_report.md` — human-readable outcome (written after verification)

## Verification steps (future / M4.5 plan)

1. manifest.json exists  
2. dump file exists  
3. schema file exists when expected  
4. checksum.sha256 exists  
5. sha256 matches dump/schema files  
6. size_bytes > 0  
7. database role is backup source  
8. backup kind is full or schema_only  
9. live_actions_enabled policy recorded  

Seed mode (`mercury backup verify-plan --demo`) models these checks only — **no files are verified** and **no database is contacted**.

## Schema-only vs full

| Kind | Verification | DR / prod-to-dev |
|------|--------------|------------------|
| **schema_only** | Can pass for schema artifact | **Not sufficient** for full DR or prod-to-dev sync |
| **full** | Must pass before trust | Required before prod-to-dev sync (with policy gates) |

Full **verified** backups are required before any prod-to-dev sync.

## Seed commands

```bash
mercury backup verify-plan --demo
mercury backup list --demo
mercury report preview --db erebus_threat_intel_prod --kind full
```

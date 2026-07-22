# Storage cleanup and destination packaging

Mercury cleanup is **preview-only** while `destination_validation_pending=true`.

## ScytaleDroid

These trees are **out of Mercury cleanup scope**:

- `scytaledroid_migration_checkpoints`
- `scytaledroid_apk_store_backups`
- `scytaledroid_artifacts`

Classification: `MANUAL_REVIEW_ONLY` + `EXCLUDE_FROM_DESTINATION`. Auto cleanup is prohibited. Destination packages exclude them unless `--allow-scytaledroid` is paired with exact `--scytaledroid-path` values.

Cross-tree APK duplication (~78.5 GiB) is informational only and is **not** treated as reclaimable by Mercury.

## Commands

```bash
./run.sh storage cleanup status
./run.sh storage cleanup preview
./run.sh storage cleanup preview --write-plan /tmp/cleanup_plan.json
./run.sh migration package preview --run-id 20260722T055400Z_phase3b
```

`storage cleanup execute` remains refused until destination validation succeeds and policy allows quarantine-only execution.

## Destination planning documents

Governed Phase 3B destination documents use schema `mercury.destination_document.v1`:

- `source_host_inventory.json`
- `environment_secret_name_inventory.json`
- `destination_acceptance_checklist.json`
- `rollback_instructions.json`

```bash
./run.sh migration documents generate --run-id 20260722T055400Z_phase3b \
  --mercury-commit <full-sha> \
  --mercury-capture-id <capture-id>
```

New generations write under `.mercury_control/destination/<run-id>/documents_runs/<stamp>/` and never overwrite the historical `documents/` tree. Package preview includes the latest complete run. Secret **names** only; values are never packaged. Destination-host fields may remain `UNRESOLVED_OPERATOR_INPUT` without blocking package membership.

## Config

See `config/retention.example.toml`. Distinguishes:

- `historical_phase3b_mercury_commit`
- `current_destination_mercury_commit` / `current_destination_mercury_capture_id`
- `historical_erebus_capture_ids` / `current_erebus_destination_commit`

## State machine

1. `DESTINATION_VALIDATION_PENDING` → preview allowed, execute refused
2. `DESTINATION_VALIDATED` → quarantine may be authorized
3. `QUARANTINE_VERIFIED_AND_COOLING_PERIOD_EXPIRED` → purge may be separately authorized

MariaDB leftover schemas are tracked separately and are never mixed into HDD cleanup.

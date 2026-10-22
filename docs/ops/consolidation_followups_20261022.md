# Consolidation follow-ups — 2026-10-22

Phase 2 implemented exclusive client defaults for both password paths, the
shell repair equivalent, new backup verification identity evidence, and the
primary-label setting on `followup/primary-label-20261022`.

Pending separately authorized operations:

- Physically relabel the existing `MERCURY_DATA_V2` disk to `MERCURY_DATA` and
  reconcile any explicit `[storage.primary].label` value. No disk changes were
  made. `MERCURY_PRIMARY_LABEL` overrides that config field; the default is
  `MERCURY_DATA`. Doctor only warns about a mismatch.
- Switch the original checkout to integration after review; no switch executed.
- Fresh backups and restore checks remain separate operations. The authenticated
  identity in a new manifest describes its verification connection, not proof
  of a historical dump session. Old identity evidence stays unknown.
- Review the worktree removal commands before manually deleting anything.
- Push branches only with explicit approval; main remains unchanged.

# Consolidation follow-ups — 2026-10-22

Phase 1 defers these items; none authorizes live operations.

- Follow-up branch: `followup/primary-label-20261022`. Add a consistent
  `MERCURY_PRIMARY_LABEL` setting in a later task. The intended final disk label
  is `MERCURY_DATA`; the current physical label remains `MERCURY_DATA_V2`.
  Coordinate any future configuration change with the separately authorized
  physical rename. No label setting or disk rename is implemented in Phase 1.
- Passwordless connections still load normal MariaDB option files, even with
  the exclusive-defaults-file fix for password-bearing connections.
- `scripts/repair-mercury-host.sh` invokes MariaDB directly, bypassing the shared
  credential helper. It remains outside this consolidation's execution scope.
- Recording requested user plus `CURRENT_USER()` needs a separate evidence
  change. The shared credential helper prepares process arguments and does not
  own a database session or verification records. File verification is offline;
  adding a probe and persisting its result crosses that boundary. A new probe
  would identify that connection, not establish the identity of a historical
  dump session. No identity-recording change is implemented in Phase 1.

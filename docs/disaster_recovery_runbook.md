# Disaster recovery runbook (outline)

> **Seed status:** This runbook is a policy outline. Mercury does not execute restore or DR steps in seed mode.

## When to use

- Production database corruption or loss
- Need to validate backups via restore-check databases
- Platform migration or Fedora deployment recovery

## High-level steps (future implementation)

1. **Assess** — Identify affected `*_prod` databases; do not touch unrelated prod systems.
2. **Locate backup** — Use configured backup root and backup history (when implemented).
3. **Restore-check** — Restore to `_restorecheck_*` temp database; verify data and schema. Successful restore-check runs auto-drop the temp database; failed runs preserve it for debugging and print the cleanup command.
4. **Promote** — Only after verification, restore into production with explicit approval.
5. **Re-sync dev** — After prod is healthy, re-run prod-to-dev sync per sync policy.

## Safety reminders

- `_restorecheck_*` databases are temporary and not backup sources.
- Never overwrite `*_prod` without verified backup and explicit approval.
- Always document actions in backup history / reports (when implemented).

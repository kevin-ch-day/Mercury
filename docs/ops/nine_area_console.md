# Mercury operator console

Mercury’s interactive main menu is organized around **backup and disaster
recovery on this host**. Workstation-move and handoff tools still exist; they
are nested under System health, not peer areas.

## Main console

| Key | Area |
|-----|------|
| 1 | Backup production |
| 2 | Repository backups |
| 3 | Disaster recovery |
| 4 | Backup storage |
| 5 | Prod-to-dev sync |
| 6 | Reports and history |
| 7 | System health |
| 0 | Exit |

Shortcut **h** still opens deploy/handoff packaging (rare host-move tools).

## What changed from the nine-area layout

| Former top-level | Now |
|------------------|------|
| Backup and verification **[1]** | **Backup production [1]** (same Backup Operations home) |
| Git and repository recovery **[3]** | **Repository backups [2]** |
| Restore and disaster recovery **[5]** | **Disaster recovery [3]** (restore-check + deploy onto this host) |
| Mercury HDD and storage **[4]** | **Backup storage [4]** (same lifecycle; disconnect is no longer the healthy-state recommendation) |
| Database sync and data movement **[2]** | **Prod-to-dev sync [5]** |
| Reports **[8]** | **Reports and history [6]** |
| System health **[9]** | **System health [7]** (includes Deploy onto this host and Workstation move) |
| Workstation migration **[6]** | Health **[7] → [8]** Workstation move and handoff |
| Deployment and handoff **[7]** | Disaster recovery **[3] → [5]** Deploy onto this host; remaining packaging via **h** / Health |

## Hub UX notes

- **[1]** opens Backup Operations directly.
- **[3]** is restore-check of production backups into disposable `_restorecheck_*`
  databases, plus deploy of verified artifacts onto this MariaDB host.
  Never restores into `*_prod` from the dashboard.
- **[4]** recommended action while backups are enabled is **Review backup storage**,
  not prepare-to-disconnect. Disconnect remains under change-mode.
- **[5]** is prod→dev refresh of disposable `*_dev` databases only.
- **[7]** health holds doctor/config plus rare deploy/move tools.

## Software-only console (backup storage absent)

Reduced set: reconnect **[1]**, disaster recovery planning **[2]**, repository
planning **[3]**, reports **[4]**, health **[5]**. Full seven-area layout
returns when backup storage is attached.

# Nine-area operator console

Mercury’s interactive main menu is organized into **nine product areas**.
This document records the old→new route map, deliberate shortcuts, and
features that still need deeper product work.

## Main console

| Key | Area |
|-----|------|
| 1 | Backup and verification |
| 2 | Database sync and data movement |
| 3 | Git and repository recovery |
| 4 | Mercury HDD and storage |
| 5 | Restore and disaster recovery |
| 6 | Workstation migration |
| 7 | Deployment and handoff |
| 8 | Reports, evidence, and history |
| 9 | System health and configuration |
| 0 | Exit |

The former **Advanced tools** hub is obsolete routing only. Capabilities remain
under the homes above and via unchanged CLI groups.

## Old → new primary homes

| Former route | New home |
|--------------|----------|
| Main 1 guided Backup and Sync / Backup Operations | **[1]** opens Backup Operations directly (Guided = Ops [1]) |
| Main 1 / Advanced → prod→dev sync | **[2]** Database sync and data movement |
| Main 1 / Advanced → offline Git | **[3]** Git and repository recovery |
| Main 2 Mercury HDD and Storage | **[4]** Mercury HDD and storage |
| Main 3 Restore and disaster recovery | **[5]** Restore and disaster recovery |
| Main 5 migration + handoff + deploy (combined) | **[6]** migration capture/validate; **[7]** deploy/handoff |
| Main 4 Reports | **[8]** Reports, evidence, and history |
| Main 6 System health | **[9]** System health and configuration |
| Advanced tools [7] (removed) | Split across [1]–[7] as above |
| “Open Advanced restore tools” | Removed; **[5]** restore-check / cleanup |
| Backup Ops restore-check / DB bundle / handoff slots | Moved: restore **[5]**; bundle+handoff **[7]** |

## Duplicate routes removed

- Advanced tools main-menu front door
- Software-only Advanced slot
- Recovery → Open Advanced restore tools
- Recovery → Open Workstation migration cross-link
- Migration hub owning handoff/deploy (moved to **[7]**)
- Health → full storage lifecycle menu (observe-only status remains; lifecycle is **[4]**)
- Backup Operations embedding restore-check, DB bundle write, and handoff open
- Nested “Restore tools (same lane)” under **[5]** (flattened; pinned CLI card instead)

## Hub UX notes (current)

- Hubs show a one-line **purpose** before choices.
- **[1]** opens **Backup Operations** directly (no intermediate Backup hub).
  Header is compact: backup root, writer, status+capacity on one status line.
  Table column is **RC** (short Passed/Pending). Focus/next-action is
  shown first. When backups are fresh but restore-check is pending, the screen
  points to Main **[5]** (with pending count) instead of another backup.
  Full backup warns before rewriting already fresh+restore-checked production.
  Long dumps print a heartbeat about every 20s so large databases do not look hung.
  Production/dev write paths auto-verify after dump; pigz is preferred for
  compress/decompress when installed.
- **Backup Operations [1] Guided backup session** is backup-first: production
  back up + verify by default; Git/sync/dev asked optionally and labeled with
  Main Menu homes. Full “recommended” multi-lane plan remains available to
  non-interactive / customize paths via `recommended_session_plan()`.
- **Backup Operations** is backup/verify only (guided, full/prod/dev write,
  verify, preview). Restore-check execution stays under Main **[5]**.
- **[2]** includes sync readiness, transfer status, transfer/handoff history, and
  write/receive command card (`transfer write` dry-run without `--execute`).
- **[3]** shows offline HDD clone status on the same screen as update/check,
  receipt, repo status, and bundle plan/actions. When copies need sync, **[1]**
  is labeled with the pending count; **[2]** re-checks. Bundle execute stays
  `repo bundle --execute`.
- **[5]** opens the consolidated **Restore and Disaster Recovery** dashboard
  (seven required databases). Focus/next-action is first; the Production table
  is actionable. Development is a one-line summary (backed up N/3 · RC deferred),
  not a per-DB table. Pending production restore-checks drive readiness and
  **[1]**; cleanup **[3]** appears only when `_restorecheck_*` schemas exist.
  Separate status-only Restore-check Operations / Disaster Recovery screens are
  removed from the menu path.
- **[7]** owns DB bundle write, handoff (including **Handoff packaging tools**),
  deploy, and cutover CLI guidance with required exact IDs.
- **[8]** includes full-backup receipt observation alongside history/protection.
- **[9]** can show local configuration status (`config show`); never prints secrets.

## Features still flagged

These remain reachable, but the interactive surface is incomplete or spans
multiple areas. Do **not** treat CLI coverage as a finished menu UX.

1. **Pinned prod/dev restore & cross-schema restore** — CLI covers these; **[5]**
   exposes a command card, not first-class interactive flows.
2. **Transfer write/receive execute** — CLI-first under `transfer *`; menu **[2]**
   shows status + command card only.
3. **Repo bundle execute / dirty worktree capture** — CLI `repo bundle --execute`;
   menu **[3]** status + plan preview + command card.
4. **Production cutover execute / acceptance / rollback** — CLI
   `production-cutover *`; menu **[7]** preview/command card only.
5. **Local configuration editor** — `config show` / `config init` only; no
   interactive TOML editor (by design for safety).
6. **Storage USB→HDD migration vs workstation migration** — storage cutover stays
   under **[4]**; workstation package/destination validation under **[6]**.

## Software-only console (HDD absent)

Reduced set: reconnect **[1]**, restore planning **[2]**, git planning **[3]**,
reports **[4]**, health **[5]** (local numbering). Full nine-area layout returns
when the Mercury HDD is attached.

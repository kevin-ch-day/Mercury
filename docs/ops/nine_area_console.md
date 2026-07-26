# Nine-area operator console

Mercury’s interactive main menu is organized into **nine product areas**.
This document records the old→new route map, deliberate shortcuts, and
features that need deeper redesign rather than silent remapping.

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
| Main 1 guided Backup and Sync / Backup Operations | **[1]** Backup and verification |
| Main 1 / Advanced → prod→dev sync | **[2]** Database sync and data movement |
| Main 1 / Advanced → offline Git | **[3]** Git and repository recovery |
| Main 2 Mercury HDD and Storage | **[4]** Mercury HDD and storage |
| Main 3 Restore and disaster recovery | **[5]** Restore and disaster recovery |
| Main 5 migration + handoff + deploy (combined) | **[6]** migration capture/validate; **[7]** deploy/handoff |
| Main 4 Reports | **[8]** Reports, evidence, and history |
| Main 6 System health | **[9]** System health and configuration |
| Advanced tools [7] (removed) | Split across [1]/[7] as above |
| “Open Advanced restore tools” | Removed; **[5] → Restore tools** |

## Duplicate routes removed

- Advanced tools main-menu front door
- Software-only Advanced slot
- Recovery → Open Advanced restore tools
- Recovery → Open Workstation migration cross-link
- Migration hub owning handoff/deploy (moved to **[7]**)
- Health → full storage lifecycle menu (observe-only status remains; lifecycle is **[4]**)

## Features flagged for redesign

These remain reachable, but the interactive surface is incomplete or spans
multiple areas. Do **not** treat CLI coverage as a finished menu UX.

1. **Guided Backup and Sync session** — still launched from **[1]**; wizard may
   still offer sync/git optional lanes. Prefer a backup-only guided path later.
2. **Backup Operations menu** — still embeds restore-check, handoff open, and DB
   bundle shortcuts. Keep as expert menu under **[1]** until those slots move.
3. **Pinned prod/dev restore & cross-schema restore** — CLI/restore-check cover
   these; menu labels do not yet expose pinned/dev/cross-schema as first-class
   items under **[5]**.
4. **Transfer write/receive execute** — CLI-first under `transfer *`; menu **[2]**
   shows status + CLI hint only.
5. **Repo bundle execute / dirty worktree capture** — CLI `repo bundle` /
   status; menu **[3]** status + offline recovery + CLI hint.
6. **Production cutover execute / acceptance / rollback** — CLI
   `production-cutover *`; menu **[7]** preview hint only.
7. **Local configuration editor** — CLI `config init|show`; menu **[9]** hint.
8. **Storage USB→HDD migration vs workstation migration** — storage cutover stays
   under **[4]**; workstation package/destination validation under **[6]**.

## Software-only console (HDD absent)

Reduced set: reconnect **[1]**, restore planning **[2]**, git planning **[3]**,
reports **[4]**, health **[5]** (local numbering). Full nine-area layout returns
when the Mercury HDD is attached.

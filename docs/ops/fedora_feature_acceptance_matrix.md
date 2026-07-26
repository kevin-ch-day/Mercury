# Track A — Fedora feature acceptance matrix

Mercury product acceptance on Fedora is separate from the completed database
migration. This matrix is the definition of "done" for Track A.

**Track B** (Debian/Mint portability) starts only after Track A passes.

## Rules

1. Exit the interactive menu with `0` unless a matrix row explicitly requires a
   menu execution path.
2. Prefer CLI equivalents that emit the same receipts as the menu.
3. Every row records: preview/status, execute (if allowed), and failure refusal.
4. Use exact backup IDs, package IDs, and commits — never unqualified `latest`
   when asserting recovery authority.
5. Do not enable application workers, intake, or schedulers during acceptance.
6. Do not reactivate the retired USB as a writer.
7. Freeze unrelated features unless a matrix row exposes a concrete gap.

## Record template

For each operation, file one row (or linked receipt) with:

| Field | Value |
|-------|-------|
| Distro | Fedora (ID/VERSION_ID from `/etc/os-release`) |
| Menu / CLI path | |
| Prerequisites | |
| Preview / status result | |
| Execute result | `PASS` / `FAIL` / `REFUSED` / `SKIPPED` |
| Receipt path(s) | |
| Rollback / cleanup result | |
| Objects changed | files / DB objects / none |
| Classification | `accepted` / `gap` / `blocked` / `obsolete` |

## Known findings

| ID | Area | Finding | Classification |
|----|------|---------|----------------|
| A-DASH-001 | Main menu status | Dashboard expected `latest_verified_backup_at` / `verified_source_count` that `StateSummary` did not populate. **Fixed** in `src/mercury/state/summary.py` (ledger-backed). Live compact line now `Verified · <timestamp>`. | **accepted** |
| A-3-02-GAP | Restore-check / development | `restore-check` refuses configured development schemas (`not an approved production backup source`) and does not honor development artifact verification. Conflicts with mandatory seven-schema platform scope. | **gap** |
| A-2-03-USB | Storage | USB is policy archive-only but physically RW without sudo remount. Doctor/storage warn correctly; transport remount still operator-gated. | **blocked** (sudo) |
| A-MSG-DEV | Backup terminal copy | Full-backup result still labels development lane “optional recovery; not default handoff” while platform docs require the three `_dev` schemas for recovery readiness. Packaging boundary vs local recovery wording needs alignment. | **gap** (wording) |

## Authority pins (Fedora source host)

| Component | Authority |
|-----------|-----------|
| Mercury | GitHub `origin/main` on the acceptance host |
| Erebus Engine | `05f3abc2dd30c57a6a303e24b90d15d7dbf3a8f9` |
| Erebus Web | `6479f66` |
| ScytaleDroid | `33c2f93` |
| ScytaleDroid Web | `d3beeec` on `agent/web-report-modularization` |
| ObsidianDroid | parent checkout; dirty `Zimperium-IOC` submodule must remain reported dirty |
| fedora-linux-scripts | out of scope for Dell-driven acceptance pushes |

## Storage prerequisites

| State | Required observation |
|-------|----------------------|
| No drives | Accurate disconnected / not mounted |
| HDD attached unmounted | Detected by UUID; writes disabled until restore |
| HDD mounted RO | Readable; backup writes refused |
| HDD mounted RW + writes restored | Active writer `primary` |
| USB absent | No legacy writer dependency |
| USB present | Archive-only; physical RO preferred before transport |

Primary UUID: `715f29a9-2671-477b-8c8d-515d190addb9` (`MERCURY_DATA_V2`).  
Legacy UUID: `e4f0c7fb-132e-4867-9c16-5e4749f5c43a` (`MERCURY_DATA_USB`).

---

## Menu 1 — Back up and sync

| ID | Case | Preview/status | Execute | Failure |
|----|------|----------------|---------|---------|
| A-1-01 | Seven-schema full backup (4 prod + 3 configured dev) | Dry-run lists all seven | Exact backup IDs, dumps, checksums, manifests, one full-run receipt + `.sha256` | Refuse when writes disabled / wrong mount |
| A-1-02 | Repeat backup | Status reflects prior verified run (see A-DASH-001) | Second run gets new exact IDs; does not overwrite prior artifacts | — |
| A-1-03 | Repo bundles for authority set | Plan shows dirty vs clean correctly | Bundles verify; dirty worktrees not labeled clean | Missing repo path refuses clearly |
| A-1-04 | No USB writer dependency | Storage shows USB archive-only | Backup/bundle write only under V2 paths | — |
| A-1-05 | Dirty Obsidian submodule | Bundle/status reports dirty + submodule divergence | Bundle still written for parent commit | Must not silently update submodule |

## Menu 2 — Mercury HDD and storage

| ID | Case | Preview/status | Execute | Failure |
|----|------|----------------|---------|---------|
| A-2-01 | Connected / unmounted / RO / RW detection | Each state labeled truthfully | Mount/reconnect only when requested | Wrong UUID refused |
| A-2-02 | Active writer validation | `primary` after restore-writes | Writes gated until `RESTORE MERCURY WRITES` | Detached/disabled blocks backup |
| A-2-03 | Legacy archive behavior | USB never active writer | Remount RO path documented/executed | RW USB warns before transport |
| A-2-04 | SMART evidence | Preview | Record on V2 only | USB not written |
| A-2-05 | Safe detach / reconnect | Pre-detach checklist | Detach → reconnect → restore writes | No USB reactivation |

## Menu 3 — Restore and disaster recovery

| ID | Case | Preview/status | Execute | Failure |
|----|------|----------------|---------|---------|
| A-3-01 | Pinned prod restore-check | Plan names exact backup ID | Import into `_restorecheck_*` only | Existing non-temp target refused |
| A-3-02 | Pinned dev restore-check | Dev allow flag required | Disposable target + verify | Prod path not used |
| A-3-03 | Cross-schema case | Erebus or Scytale + PI dependency noted | Validate both schemas as needed | Missing dependency blocks clearly |
| A-3-04 | Existing-target refusal | Collision detected | No overwrite | Receipt records refusal |
| A-3-05 | Partial failure cleanup | — | Failed restore-check preserves debug DB + prints cleanup | No silent drop of unrelated schemas |

## Menu 4 — Reports and backup history

| ID | Case | Preview/status | Execute | Failure |
|----|------|----------------|---------|---------|
| A-4-01 | Backup history lists today's seven IDs | Exact IDs visible | — | — |
| A-4-02 | Full-backup receipt classification | Governed vs invalid maintenance separated | Observe-only quarantine plan | REFUSED receipts not treated as handoff evidence |
| A-4-03 | Manifest/checksum verify | Per-DB verify PASS | — | Tamper/mismatch fails closed |
| A-4-04 | Main menu last-backup line | Matches ledger / receipt truth | — | **A-DASH-001 fixed** (ledger-backed `StateSummary`) |

## Menu 5 — Workstation migration

| ID | Case | Preview/status | Execute | Failure |
|----|------|----------------|---------|---------|
| A-5-01 | Package status / next | Destination validation pending shown honestly | No silent package rewrite | — |
| A-5-02 | Source capture / package create | Preview membership | Only when explicitly in scope for this host | Secrets never packaged as values |
| A-5-03 | Package verify | Checksums | — | Corrupt member fails |
| A-5-04 | Destination restore / cutover | Readiness checklist | Not re-run casually on Dell source host | USB not writer |

Migration saga need not be repeated end-to-end on Dell; rows may `SKIPPED` with pointer to Asus package evidence when destructive or destination-only.

## Menu 6 — System health and configuration

| ID | Case | Preview/status | Execute | Failure |
|----|------|----------------|---------|---------|
| A-6-01 | Doctor with V2 mounted RW | Sources present; writer primary | — | — |
| A-6-02 | Doctor missing config | Clear blockers | Repair plan printed, not auto-executed | — |
| A-6-03 | No retired USB reactivation | Repair plan omits USB-as-writer | — | Any USB writer suggestion = gap |
| A-6-04 | Destination-local inventory | repos/databases paths reported | — | — |

## Menu 7 — Advanced tools

| ID | Case | Preview/status | Execute | Failure |
|----|------|----------------|---------|---------|
| A-7-01 | Inventory all advanced commands | Supported / experimental / internal / obsolete labels | — | — |
| A-7-02 | Bypass attempt | — | Advanced path cannot skip write gates or safety policy | Ungoverned destructive path = gap |

---

## Execution log (Fedora 43 · Dell · 2026-07-26)

Distro: Fedora 43 (`ID=fedora`). Host: Dell Inspiron 15 7000 Gaming. Writer: `MERCURY_DATA_V2` primary.

| ID | Path | Preview | Execute | Receipts / notes | Classification |
|----|------|---------|---------|------------------|----------------|
| A-DASH-001 | dashboard / `StateSummary` | Was false “No recent verified backup” | Fixed + unit tests | Live: `Verified · 7/26/2026 1:34 PM CDT` after repeat backup | **accepted** |
| Doc sync | `erebus_source_capture.md`, `post_cutover_storage.md` | — | Updated to real capture + mandatory seven DBs + retired USB | — | **accepted** |
| A-1-01 | `backup full --include-dev` | Dry-run 7 planned | Prior run `20260726T180739Z_full_backup` PASS | Receipt + sha256 on V2 | **accepted** |
| A-1-02 | repeat `backup full --include-dev` | Dashboard shows verified timestamp | `20260726T183046Z_full_backup` PASS; new exact IDs | `/mnt/MERCURY_DATA_V2/.mercury_control/full_backup_runs/20260726T183046Z_full_backup.json` sha256 `96245dba…ad9b39` | **accepted** |
| A-1-03 / A-1-05 | `repo bundle` (prior consolidation) | Dirty Mercury + Obsidian reported | Bundles verified; dirty not labeled clean | Manifest `repo_transfer_manifest_20260726_181227.json` | **accepted** |
| A-1-04 | storage + backup paths | USB policy archive-only | Artifacts only under V2 | Doctor warns physical USB RW | **accepted** (policy) |
| A-4-01 / A-4-02 / A-4-03 | `backup full-receipts` + `backup verify --backup-id` | Governed vs invalid separated | All 7 new IDs verify PASS | Receipt plan shows 4 governed PASS including today’s two runs | **accepted** |
| A-4-04 | main-menu last-backup line | — | Truthful after A-DASH-001 fix | — | **accepted** |
| A-3-01 | `restore-check run --execute` erebus prod | Plan allowed | PASS; temp DB dropped | `erebus_threat_intel_prod-full-20260726_183103_691` | **accepted** |
| A-3-02 | restore-check android_permission_intel_dev | Plan **refused** (not prod backup source) | Not executed | Needs development restore-check lane | **gap** |
| A-3-03 | restore-check android_permission_intel | Plan allowed | PASS; temp DB dropped | Cross-schema with Erebus prod drill | **accepted** |
| A-3-04 | bogus backup-id plan | `allowed: False` | — | Refuses closed | **accepted** |
| A-2-02 | write preflight / reconnect | Writes restored earlier | Backup allowed when mounted+restored | — | **accepted** |
| A-2-03 | USB archive | Policy RO; physical RW | Remount RO needs sudo | `archive-remount-ro` preview OK | **blocked** |
| A-2-05 | `storage detach status/preview` | Correctly **refuses** while writes enabled | Full detach/reconnect not executed this pass | Preflight `DETACH_BLOCKED_*` / writes not disabled | **blocked** (deferred; gates work) |
| A-6-01 / A-6-03 | `doctor` | 4/4 verified prod; USB warn; no USB-as-writer reactivation | — | Actionable blockers: none | **accepted** |
| A-7-01 | `mercury --help` inventory | Top-level commands listed | Label taxonomy (supported/experimental/obsolete) not yet formalized | — | **gap** (inventory incomplete) |
| A-7-02 | detach/sync gates | Destructive paths gated | No workers enabled | — | **accepted** (spot) |

### Repeat-run backup IDs (`20260726T183046Z_full_backup`)

- `android_permission_intel-full-20260726_183046_308`
- `erebus_threat_intel_prod-full-20260726_183103_691`
- `obsidiandroid_core_prod-full-20260726_183227_625`
- `scytaledroid_core_prod-full-20260726_183228_046`
- `android_permission_intel_dev-full-20260726_183311_631`
- `erebus_threat_intel_dev-full-20260726_183315_592`
- `scytaledroid_core_dev-full-20260726_183421_637`

### Track A status after this pass

**Not passed yet.** Open required gaps/blockers:

1. Development restore-check support (A-3-02) — blocks exit criterion #3.
2. USB physical RO + full detach/reconnect cycle (A-2-03 / A-2-05).
3. Optional wording: development “optional” terminal copy (A-MSG-DEV).
4. Advanced-command taxonomy labels (A-7-01).

Track B (`run.sh` os-release adapters) remains deferred.

## Track A exit criteria

Track A **passes** when:

1. Every non-skipped row is `accepted` or has an explicit waived gap with owner.
2. A-DASH-001 is fixed or waived with dashboard truthfulness restored.
3. At least one prod restore-check, one dev restore-check, and one cross-schema case pass.
4. Safe detach/reconnect works with USB archive-only and V2 as sole writer.
5. No matrix row required reactivating legacy USB writes.

## Track B (later)

`run.sh` reads `/etc/os-release`, sets `MERCURY_DISTRO_FAMILY` / package manager, loads a thin adapter, and launches the same Python CLI. Repeat this matrix on disposable Mint after Track A.

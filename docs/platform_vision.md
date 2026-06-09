# Platform vision

Mercury is a **Fedora-first operations utility** for the Android security research platform. It is not an AI tool, malware analyzer, web app, or GitHub status dashboard.

## Platform databases

| Project | Databases | Role |
|---------|-----------|------|
| **Erebus** | `erebus_threat_intel_prod` / `_dev` | VT enrichment, malware catalog, Permission Intel writes |
| **Platform** | `android_permission_intel` | Shared permission authority (Erebus, ScytaleDroid, Iapetus) |
| **ScytaleDroid** | `scytaledroid_core_prod` / `_dev` | APK static/dynamic analysis |
| **ObsidianDroid** | `gecko_research_database_prod` / `_dev` | Malware ML/research (out of current Mercury milestone scope; visible only for operator awareness if discovered live) |
| **Iapetus** | (future) | Deep-learning kernel — seed repo only |

## Mercury priorities (in order)

1. Detect database environment  
2. Discover and classify databases  
3. Back up production / source-of-truth only  
4. Export schema-only copies  
5. Verify backups and manifests  
6. Restore-test backups  
7. Sync prod → disposable `*_dev`  
8. Reports — what is protected and what is not  

## Seed phase (now)

- Menu-driven CLI (`mercury menu`)  
- Config + catalog discovery (`mercury db discover --demo`)  
- Dry-run backup plans (`mercury backup plan --demo`)  
- Read-only MariaDB discovery implemented; live writes still gated  
- No destructive actions; no live connections by default  

## Current Fedora milestone

- Read-only local MariaDB discovery
- Classification of active platform databases
- USB-backed full logical backups for `erebus_threat_intel_prod`, `scytaledroid_core_prod`, and `android_permission_intel`
- Backup verification and protection reporting
- Prod->dev sync readiness for the Erebus and ScytaleDroid pairs only

## Later Fedora phase

- `mercury db discover` — read-only `SHOW DATABASES`  
- `mercury backup run --db <prod_db>` — mariadb-dump logical backups  
- `mercury backup verify --latest`  
- `mercury sync-dev --source <prod> --target <dev>` — requires `SYNC DEV` confirmation  

Windows and non-Fedora Linux are for seed development/status only. Live Mercury operations are Fedora-only.

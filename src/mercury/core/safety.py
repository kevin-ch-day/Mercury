"""Core safety policy constants for Mercury."""

MODE_SEED = "seed"
DRY_RUN_ONLY = True
LIVE_ACTIONS_ENABLED = False

# Future prod-to-dev sync confirmation (not used in seed)
SYNC_DEV_CONFIRMATION_PHRASE = "SYNC DEV"

POLICY_SUMMARY = """
Mercury safety policy:
- First priority: protect production / source-of-truth databases.
- Back up only production and designated shared authority DBs (e.g. android_permission_intel).
- Never back up *_dev databases by default (*_dev are disposable sync targets).
- Never drop or overwrite *_prod.
- Never restore into *_prod by default.
- Always backup and verify the source before syncing into dev.
- Show source and target before destructive actions; require typing SYNC DEV for dev sync.
- Seed mode: dry-run and discovery-only; no destructive actions; no live DB connections unless enabled.
""".strip()

SAFETY_NOTES = [
    "Backups write to USB when MariaDB, config, and backup root are valid.",
    "Production (*_prod) and shared authority DBs are backup sources.",
    "Development (*_dev) DBs are excluded from backup; they are disposable refresh targets rebuilt from verified source backups.",
    "Never drop or overwrite *_prod.",
    "Verify source backups before any prod-to-dev sync so dev refresh never runs without source protection.",
    "Restore-check temp DBs (_restorecheck_*) are never backup sources.",
    "Prod-to-dev sync, deploy, and restore require explicit confirmation and live_actions_enabled.",
    "Future backups: mariadb-dump/mysqldump logical full + schema-only (Fedora).",
]

BACKUP_KIND_FULL = "full"
BACKUP_KIND_SCHEMA_ONLY = "schema_only"

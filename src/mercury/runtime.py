"""Runtime mode and operator status for menu/CLI."""

from mercury.paths import LOCAL_CONFIG
from mercury.safety import DRY_RUN_ONLY, LIVE_ACTIONS_ENABLED, MODE_SEED

try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment]


def _backup_root_from_config() -> str | None:
    if tomllib is None or not LOCAL_CONFIG.exists():
        return None
    with LOCAL_CONFIG.open("rb") as handle:
        data = tomllib.load(handle)
    mercury = data.get("mercury", {})
    if isinstance(mercury, dict):
        root = mercury.get("backup_root")
        if root and str(root).strip():
            return str(root).strip()
    return None


def operator_status(*, database_connected: bool = False) -> dict[str, str]:
    """Status lines for menu and env probe."""
    backup_root = _backup_root_from_config()
    if DRY_RUN_ONLY or not LIVE_ACTIONS_ENABLED:
        safety = "dry-run only"
        mode = f"{MODE_SEED} / dry-run"
    else:
        safety = "live actions enabled"
        mode = "operational"

    return {
        "mode": mode,
        "database": "connected" if database_connected else "not connected",
        "backup_root": backup_root if backup_root else "not configured",
        "safety": safety,
    }

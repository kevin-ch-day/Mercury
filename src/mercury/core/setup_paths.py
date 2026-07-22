"""Operator storage and configured path assessment for doctor and dashboard."""

from __future__ import annotations

from pathlib import Path

from mercury.core.path_permissions import PathPermissionCheck, check_path_permission, safe_ensure_directory
from mercury.core.paths import REPO_ROOT
from mercury.logging.config import load_mercury_section, resolve_log_dir

MERCURY_USB_DIR_LABELS: tuple[tuple[str, str], ...] = (
    ("mercury_backups", "Operator backup root"),
    ("mercury_logs", "Operator log directory"),
    ("mercury_manifests", "Operator manifest directory"),
    ("mercury_repo_backups", "Operator repo backup directory"),
    ("mercury_restore_checks", "Operator restore-check directory"),
    ("mercury_runbooks", "Operator runbook directory"),
    ("mercury_state", "Operator state directory"),
)

MERCURY_OPERATOR_STORAGE_DIRS = (
    "mercury_logs",
    "mercury_backups",
    "mercury_manifests",
    "mercury_state",
    "mercury_repo_backups",
    "mercury_restore_checks",
    "mercury_runbooks",
)

# Compatibility import for older repair/doctor call sites.
MERCURY_USB_CHOWN_DIRS = MERCURY_OPERATOR_STORAGE_DIRS


def _usb_layout_permissions_in_scope() -> bool:
    """After HDD cutover, USB layout is archive-only — do not treat it as operator health."""
    try:
        from mercury.core.storage_roots import load_storage_config
        from mercury.core.storage_roles import StorageWriteRole

        cfg = load_storage_config(warn_deprecated=False)
        if cfg.cutover_complete and cfg.active_write_role == StorageWriteRole.PRIMARY:
            return False
    except Exception:
        pass
    return True


def assess_mercury_path_permissions(
    *,
    policy,
    usb,
    self_heal: bool = False,
    healed: list[str] | None = None,
) -> list[PathPermissionCheck]:
    checks: list[PathPermissionCheck] = []

    if usb.mercury_layout_present and _usb_layout_permissions_in_scope():
        for dirname, label in MERCURY_USB_DIR_LABELS:
            path = usb.mount_path / dirname
            if self_heal and not path.exists():
                ok, message = safe_ensure_directory(path)
                if ok and message == "created" and healed is not None:
                    healed.append(f"Created {path}")
            checks.append(check_path_permission(path, label=label))

    if policy.config_path is not None:
        checks.append(check_path_permission(policy.backup_root, label="configured backup root"))
        checks.append(check_path_permission(resolve_log_dir(), label="configured log directory"))
        section = load_mercury_section()
        for key, label in (
            ("manifest_dir", "configured manifest directory"),
            ("runbook_dir", "configured runbook directory"),
            ("repo_backup_root", "configured repo backup directory"),
        ):
            raw = section.get(key)
            if raw and str(raw).strip():
                path = Path(str(raw).strip())
                if self_heal and not path.exists():
                    ok, message = safe_ensure_directory(path)
                    if ok and message == "created" and healed is not None:
                        healed.append(f"Created {path}")
                checks.append(check_path_permission(path, label=label))
    elif not usb.mercury_layout_present:
        fallback = REPO_ROOT / "backups"
        if self_heal:
            ok, message = safe_ensure_directory(fallback)
            if ok and message == "created" and healed is not None:
                healed.append(f"Created {fallback}")
        checks.append(check_path_permission(fallback, label="repo-local backup fallback"))

    return checks


def permission_repair_blockers(checks: list[PathPermissionCheck]) -> list[str]:
    blockers: list[str] = []
    for check in checks:
        if check.needs_repair:
            blockers.append(f"{check.label} not usable — {check.detail} ({check.path})")
    return blockers

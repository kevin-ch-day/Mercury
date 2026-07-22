"""Initialize local config files from examples."""

from __future__ import annotations

import getpass
import os
import shutil
from pathlib import Path

from mercury.core.environment_status import discover_usb_target
from mercury.core.usb_mount import default_usb_path_replacements
from mercury.core.setup_paths import MERCURY_USB_DIR_LABELS
from mercury.core.path_permissions import safe_ensure_directory
from mercury.core.paths import (
    DATABASES_EXAMPLE,
    DATABASES_LOCAL,
    LOCAL_CONFIG,
    LOCAL_EXAMPLE,
    LOGS_DIR,
    REPO_ROOT,
    REPOS_EXAMPLE,
    REPOS_LOCAL,
)

_USB_PATH_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ('backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"', 'backup_root = "{backup_root}"'),
    ('log_dir = "/mnt/MERCURY_DATA_USB/mercury_logs"', 'log_dir = "{log_dir}"'),
    ('repo_backup_root = "/mnt/MERCURY_DATA_USB/mercury_repo_backups"', 'repo_backup_root = "{repo_backup_root}"'),
    ('manifest_dir = "/mnt/MERCURY_DATA_USB/mercury_manifests"', 'manifest_dir = "{manifest_dir}"'),
    ('runbook_dir = "/mnt/MERCURY_DATA_USB/mercury_runbooks"', 'runbook_dir = "{runbook_dir}"'),
)

MERCURY_USB_ARTIFACT_KEYS: tuple[str, ...] = (
    "repo_backup_root",
    "manifest_dir",
    "runbook_dir",
)


def _load_local_toml() -> dict[str, object]:
    if not LOCAL_CONFIG.exists():
        return {}
    import tomllib

    with LOCAL_CONFIG.open("rb") as handle:
        return tomllib.load(handle)


def missing_mercury_usb_artifact_keys(*, local_config: Path | None = None) -> list[str]:
    """Return [mercury] keys missing from an existing local.toml."""
    config_path = local_config or LOCAL_CONFIG
    if not config_path.exists():
        return list(MERCURY_USB_ARTIFACT_KEYS)
    import tomllib

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    section = data.get("mercury")
    if not isinstance(section, dict):
        return list(MERCURY_USB_ARTIFACT_KEYS)
    return [
        key
        for key in MERCURY_USB_ARTIFACT_KEYS
        if not section.get(key) or not str(section.get(key)).strip()
    ]


def _resolve_usb_mount_for_repair(mercury_section: dict[str, object]):
    from mercury.core.usb_mount import resolve_usb_mount

    configured_root = mercury_section.get("backup_root")
    if configured_root and str(configured_root).strip():
        backup_root = Path(str(configured_root).strip()).expanduser()
        if backup_root.name == "mercury_backups":
            return backup_root.parent.resolve()
    usb = discover_usb_target()
    if usb.mercury_layout_present:
        return usb.mount_path.resolve()
    return resolve_usb_mount()


def _append_mercury_keys(text: str, additions: dict[str, str]) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_mercury = False
    inserted = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[mercury]":
            in_mercury = True
        elif stripped.startswith("[") and in_mercury and not inserted:
            for key in MERCURY_USB_ARTIFACT_KEYS:
                if key in additions:
                    out.append(f'{key} = "{additions[key]}"')
            if additions:
                out.append("")
            inserted = True
            in_mercury = False
        out.append(line)
    if in_mercury and not inserted:
        for key in MERCURY_USB_ARTIFACT_KEYS:
            if key in additions:
                out.append(f'{key} = "{additions[key]}"')
    return "\n".join(out).rstrip() + "\n"


def missing_storage_section(*, local_config: Path | None = None) -> bool:
    """True when local.toml has no [storage] section yet."""
    config_path = local_config or LOCAL_CONFIG
    if not config_path.exists():
        return True
    import tomllib

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return not isinstance(data.get("storage"), dict)


def repair_local_config_paths() -> list[str]:
    """
    Add missing operator-storage artifact paths to an existing local.toml without overwriting
    operator settings. Also ensure a baseline [storage] section exists when absent.
    """
    if not LOCAL_CONFIG.exists():
        return ["local.toml: not found — run: mercury config init"]

    data = _load_local_toml()
    mercury = data.get("mercury")
    if not isinstance(mercury, dict):
        return ["local.toml: [mercury] section missing — run: mercury config init"]

    notes: list[str] = []
    text = LOCAL_CONFIG.read_text(encoding="utf-8")

    if missing_storage_section():
        storage_block = _default_storage_toml_block(mercury)
        text = text.rstrip() + "\n\n" + storage_block
        notes.append("local.toml: added baseline [storage] section (active_write_role=legacy)")

    missing = missing_mercury_usb_artifact_keys()
    if missing:
        mount = _resolve_usb_mount_for_repair(mercury)
        paths = default_usb_path_replacements(mount)
        additions = {key: paths[key] for key in missing}
        text = _append_mercury_keys(text, additions)
        notes.extend(f"local.toml: added {key} = {paths[key]!r}" for key in missing)
    elif not notes:
        notes.append("local.toml: all operator-storage artifact paths already present")

    LOCAL_CONFIG.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

    usb = discover_usb_target()
    if usb.mercury_layout_present:
        notes.extend(_ensure_usb_layout(usb.mount_path))
    return notes


def _default_storage_toml_block(mercury: dict[str, object]) -> str:
    """Minimal [storage] block aligned with current transitional USB writer."""
    from mercury.core.storage_roles import (
        DEFAULT_LEGACY_LABEL,
        DEFAULT_LEGACY_UUID,
        DEFAULT_PRIMARY_LABEL,
        DEFAULT_PRIMARY_MOUNT,
        DEFAULT_PRIMARY_UUID,
    )

    backup_root = mercury.get("backup_root")
    legacy_mount = "/mnt/MERCURY_DATA_USB"
    if backup_root and str(backup_root).strip():
        backup = Path(str(backup_root).strip()).expanduser()
        if backup.name == "mercury_backups":
            legacy_mount = str(backup.parent)
    return f"""# Added by config repair-local — writers remain on legacy until cutover.
[storage]
active_write_role = "legacy"
migration_state = "not_started"

[storage.primary]
role = "canonical"
label = "{DEFAULT_PRIMARY_LABEL}"
mount_path = "{DEFAULT_PRIMARY_MOUNT}"
filesystem_uuid = "{DEFAULT_PRIMARY_UUID}"
filesystem_type = "ext4"
writable = true

[storage.legacy]
role = "transition_source"
label = "{DEFAULT_LEGACY_LABEL}"
mount_path = "{legacy_mount}"
filesystem_uuid = "{DEFAULT_LEGACY_UUID}"
filesystem_type = "ext4"
writable = true

[storage.space_policy]
minimum_free_bytes = 21474836480
minimum_free_percent = 10
"""


def init_local_config(*, force: bool = False) -> list[str]:
    """
    Copy example config files to gitignored local paths.

    Returns list of human-readable results per file.
    """
    results: list[str] = []
    pairs = [
        (DATABASES_EXAMPLE, DATABASES_LOCAL, "databases.toml"),
        (REPOS_EXAMPLE, REPOS_LOCAL, "repos.toml"),
        (LOCAL_EXAMPLE, LOCAL_CONFIG, "local.toml"),
    ]
    for src, dest, label in pairs:
        if not src.exists():
            results.append(f"{label}: skipped (example missing: {src.name})")
            continue
        if dest.exists() and not force:
            results.append(f"{label}: already exists ({dest})")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        results.append(f"{label}: created from {src.name}")
        if dest == LOCAL_CONFIG:
            results.extend(_customize_created_local_config(dest))

    primary = discover_primary_operator_root()
    usb = discover_usb_target()
    if primary is not None:
        results.append(
            f"Canonical HDD layout detected at {primary} — local.toml uses primary HDD paths."
        )
    elif usb.mercury_layout_present:
        results.extend(_ensure_usb_layout(usb.mount_path))
        results.append(
            f"Operator backup layout detected at {usb.mount_path} — local.toml uses legacy USB paths."
        )
    elif LOCAL_CONFIG.exists():
        results.append(
            "Operator backup mount not detected — local.toml uses temporary repo-local paths "
            "(dev/dry-run only; not production protection)."
        )
    return results


def _customize_created_local_config(path) -> list[str]:
    """Adjust a freshly copied local.toml for the current operator host."""
    notes: list[str] = []
    text = path.read_text(encoding="utf-8")
    os_user = getpass.getuser()
    primary = discover_primary_operator_root()
    usb = discover_usb_target()

    if primary is not None:
        text = _apply_primary_paths(text, primary)
        notes.append(f"local.toml: using canonical HDD paths from {primary}.")
    elif not usb.mercury_layout_present:
        text = _apply_repo_local_paths(text)
        notes.append(
            "local.toml: using repo-local backup/log paths (temporary dev fallback)."
        )
    else:
        text = _apply_usb_paths(text, usb.mount_path)
        notes.append(f"local.toml: using {usb.mount_path} paths from mounted operator storage.")

    if 'user = "root"' in text and os_user != "root":
        text = text.replace('user = "root"', f'user = "{os_user}"', 1)
        notes.append(
            f"local.toml: set [mariadb].user to {os_user!r} "
            "(create a matching MariaDB unix_socket user: ./run.sh doctor --repair-plan)."
        )
    elif 'user = "root"' in text:
        notes.append(
            'local.toml: MariaDB user is "root" — only works when Mercury runs as OS root on Fedora.'
        )

    path.write_text(text, encoding="utf-8")
    return notes


def _apply_usb_paths(text: str, mount_path) -> str:
    text = _apply_mercury_paths(text, mount_path)
    # Keep [storage.legacy] aligned with the active transitional USB mount.
    return text.replace(
        'mount_path = "/mnt/MERCURY_DATA_USB"',
        f'mount_path = "{Path(mount_path).resolve()}"',
    )


def _apply_mercury_paths(text: str, mount_path) -> str:
    paths = default_usb_path_replacements(mount_path)
    for old, template in _USB_PATH_REPLACEMENTS:
        text = text.replace(old, template.format(**paths))
    return text


def discover_primary_operator_root() -> Path | None:
    """Return a mounted canonical HDD that already has the Mercury layout.

    Config initialization is run on a freshly cloned receiver as well as on
    legacy USB hosts.  Prefer the explicitly configured primary mount when it
    is a real mount with backup data, so a receiver does not silently create a
    legacy-USB writer configuration.
    """
    from mercury.core.storage_roles import DEFAULT_PRIMARY_MOUNT, ENV_PRIMARY_MOUNT

    mount = Path(os.environ.get(ENV_PRIMARY_MOUNT, DEFAULT_PRIMARY_MOUNT)).expanduser()
    if not mount.is_mount():
        return None
    if (mount / "mercury_backups").is_dir():
        return mount.resolve()
    return None


def _apply_primary_paths(text: str, mount_path: Path) -> str:
    """Point a fresh config at an already-cut-over canonical HDD."""
    text = _apply_mercury_paths(text, mount_path)
    text = text.replace('active_write_role = "legacy"', 'active_write_role = "primary"', 1)
    text = text.replace('migration_state = "not_started"', 'migration_state = "cutover_complete"', 1)
    text = text.replace('role = "transition_source"', 'role = "legacy_archive"', 1)
    # This replacement deliberately includes the following table header, so it
    # changes only the legacy role's writable policy, not the primary's.
    text = text.replace(
        'filesystem_type = "ext4"\nwritable = true\n\n[storage.space_policy]',
        'filesystem_type = "ext4"\nwritable = false\n\n[storage.space_policy]',
        1,
    )
    return text


def _apply_repo_local_paths(text: str) -> str:
    paths = {
        "backup_root": str((REPO_ROOT / "backups").resolve()),
        "log_dir": str(LOGS_DIR.resolve()),
        "repo_backup_root": str((REPO_ROOT / "backups" / "repo").resolve()),
        "manifest_dir": str((REPO_ROOT / "output" / "manifests").resolve()),
        "runbook_dir": str((REPO_ROOT / "output" / "runbooks").resolve()),
    }
    for old, template in _USB_PATH_REPLACEMENTS:
        text = text.replace(old, template.format(**paths))
    return text


def _ensure_usb_layout(mount_path) -> list[str]:
    notes: list[str] = []
    from pathlib import Path

    from mercury.core.usb_mount import storage_mount_is_active

    root = Path(mount_path)
    if not storage_mount_is_active(root):
        notes.append(
            f"Skipped creating layout under {root}: not an active mount "
            "(refusing host-local shadow directories)."
        )
        return notes
    for dirname, label in MERCURY_USB_DIR_LABELS:
        path = root / dirname
        ok, message = safe_ensure_directory(path)
        if message == "created":
            notes.append(f"Created USB {label}: {path}")
        elif not ok:
            notes.append(f"Could not create {label} at {path}: {message}")
    return notes

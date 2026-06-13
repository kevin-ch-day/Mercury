"""Initialize local config files from examples."""

from __future__ import annotations

import getpass
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


def repair_local_config_paths() -> list[str]:
    """
    Add missing USB artifact paths to an existing local.toml without overwriting
    operator settings.
    """
    if not LOCAL_CONFIG.exists():
        return ["local.toml: not found — run: mercury config init"]

    data = _load_local_toml()
    mercury = data.get("mercury")
    if not isinstance(mercury, dict):
        return ["local.toml: [mercury] section missing — run: mercury config init"]

    missing = missing_mercury_usb_artifact_keys()
    if not missing:
        return ["local.toml: all USB artifact paths already present"]

    mount = _resolve_usb_mount_for_repair(mercury)
    paths = default_usb_path_replacements(mount)
    additions = {key: paths[key] for key in missing}
    text = LOCAL_CONFIG.read_text(encoding="utf-8")
    LOCAL_CONFIG.write_text(_append_mercury_keys(text, additions), encoding="utf-8")

    notes = [f"local.toml: added {key} = {paths[key]!r}" for key in missing]
    usb = discover_usb_target()
    if usb.mercury_layout_present:
        notes.extend(_ensure_usb_layout(usb.mount_path))
    return notes


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

    usb = discover_usb_target()
    mount_label = str(usb.mount_path)
    if usb.mercury_layout_present:
        results.extend(_ensure_usb_layout(usb.mount_path))
        results.append(
            f"USB backup layout detected at {usb.mount_path} — "
            f"local.toml uses {mount_label} paths."
        )
    elif LOCAL_CONFIG.exists():
        results.append(
            "USB backup mount not detected — local.toml uses temporary repo-local paths "
            "(dev/dry-run only; not production protection)."
        )
    return results


def _customize_created_local_config(path) -> list[str]:
    """Adjust a freshly copied local.toml for the current operator host."""
    notes: list[str] = []
    text = path.read_text(encoding="utf-8")
    os_user = getpass.getuser()
    usb = discover_usb_target()

    if not usb.mercury_layout_present:
        text = _apply_repo_local_paths(text)
        notes.append(
            "local.toml: using repo-local backup/log paths (temporary dev fallback)."
        )
    else:
        text = _apply_usb_paths(text, usb.mount_path)
        notes.append(f"local.toml: using {usb.mount_path} paths from mounted USB.")

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
    paths = default_usb_path_replacements(mount_path)
    for old, template in _USB_PATH_REPLACEMENTS:
        text = text.replace(old, template.format(**paths))
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
    for dirname, label in MERCURY_USB_DIR_LABELS:
        path = mount_path / dirname
        ok, message = safe_ensure_directory(path)
        if message == "created":
            notes.append(f"Created USB {label}: {path}")
    return notes

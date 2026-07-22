"""Remount the USB recovery archive read-only (post-cutover transport hygiene).

Mercury already refuses writes to a legacy_archive root. Remounting the kernel
filesystem read-only closes the remaining gap where other processes could still
modify the USB. Mercury never edits fstab here — only an optional live remount.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.core.storage_roles import StorageRootRole, StorageWriteRole
from mercury.storage.archive_receipt import _mount_mode

ARCHIVE_REMOUNT_RO_CONFIRMATION = "REMOUNT ARCHIVE RO"


@dataclass(frozen=True)
class ArchiveRemountPlan:
    mount_path: Path
    filesystem_uuid: str
    label: str
    current_mode: str
    cutover_complete: bool
    legacy_is_archive: bool
    remount_command: str
    confirmation_phrase: str = ARCHIVE_REMOUNT_RO_CONFIRMATION
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers and self.current_mode == "read-write"

    @property
    def already_read_only(self) -> bool:
        return self.current_mode == "read-only"


@dataclass(frozen=True)
class ArchiveRemountResult:
    plan: ArchiveRemountPlan
    executed: bool
    success: bool
    message: str
    mode_after: str | None = None


def build_archive_remount_plan(*, config: StorageConfig | None = None) -> ArchiveRemountPlan:
    cfg = config or load_storage_config(warn_deprecated=False)
    legacy = cfg.legacy
    mode = _mount_mode(legacy.mount_path)
    blockers: list[str] = []
    notes: list[str] = [
        "Mercury does not edit /etc/fstab. Persist ro in fstab separately if desired.",
        "Requires sudo. Review the command before applying.",
    ]
    try:
        from mercury.storage.report import suggested_legacy_archive_fstab_line

        notes.append(f"Suggested fstab draft (not applied): {suggested_legacy_archive_fstab_line(cfg)}")
    except Exception:
        pass
    if not cfg.cutover_complete:
        blockers.append("Cutover is not complete — remount archive RO only after HDD is the active writer.")
    if cfg.active_write_role != StorageWriteRole.PRIMARY:
        blockers.append("Active write role is not primary.")
    if legacy.role != StorageRootRole.LEGACY_ARCHIVE:
        blockers.append(f"Legacy role is {legacy.role.value}, expected legacy_archive.")
    if not legacy.mount_path.exists():
        blockers.append(f"Legacy mount path missing: {legacy.mount_path}")
    elif mode == "unknown":
        blockers.append(f"Could not determine mount mode for {legacy.mount_path}.")
    elif mode == "read-only":
        notes.append("USB archive is already mounted read-only.")

    command = f"sudo mount -o remount,ro {legacy.mount_path}"
    return ArchiveRemountPlan(
        mount_path=legacy.mount_path,
        filesystem_uuid=legacy.filesystem_uuid,
        label=legacy.label,
        current_mode=mode,
        cutover_complete=cfg.cutover_complete,
        legacy_is_archive=legacy.role == StorageRootRole.LEGACY_ARCHIVE,
        remount_command=command,
        blockers=tuple(blockers),
        notes=tuple(notes),
    )


def execute_archive_remount_ro(
    *,
    confirmation: str,
    config: StorageConfig | None = None,
    runner: subprocess.run | None = None,
) -> ArchiveRemountResult:
    """Remount the USB archive read-only. Never touches the primary HDD writer."""
    plan = build_archive_remount_plan(config=config)
    if plan.already_read_only and not plan.blockers:
        return ArchiveRemountResult(
            plan=plan,
            executed=False,
            success=True,
            message="USB archive already mounted read-only.",
            mode_after=plan.current_mode,
        )
    if plan.blockers:
        return ArchiveRemountResult(
            plan=plan,
            executed=False,
            success=False,
            message="; ".join(plan.blockers),
            mode_after=plan.current_mode,
        )
    if (confirmation or "").strip() != ARCHIVE_REMOUNT_RO_CONFIRMATION:
        return ArchiveRemountResult(
            plan=plan,
            executed=False,
            success=False,
            message=f"Confirmation mismatch — type {ARCHIVE_REMOUNT_RO_CONFIRMATION!r}.",
            mode_after=plan.current_mode,
        )

    run = runner or subprocess.run
    completed = run(
        ["sudo", "mount", "-o", "remount,ro", str(plan.mount_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    mode_after = _mount_mode(plan.mount_path)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "mount failed").strip()
        return ArchiveRemountResult(
            plan=plan,
            executed=True,
            success=False,
            message=detail,
            mode_after=mode_after,
        )
    ok = mode_after == "read-only"
    return ArchiveRemountResult(
        plan=plan,
        executed=True,
        success=ok,
        message="USB archive remounted read-only." if ok else f"mount exited 0 but mode is {mode_after}.",
        mode_after=mode_after,
    )

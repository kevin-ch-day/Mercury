"""Dry-run migration planner: legacy USB → primary HDD (no copies).

Inventories all content under the legacy mount, classifies each path against the
primary mount, and reports space/conflicts. Never writes to either volume.
Primary ``.mercury_control`` is excluded from migration equality.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from mercury.core.paths import OUTPUT_DIR
from mercury.core.storage_roles import CONTROL_DIRNAME, EPHEMERAL_TOP_DIRS, MigrationState
from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.core.storage_space import SpaceAssessment, assess_space
from mercury.core.storage_validate import validate_storage_mount


class ConflictPolicy(StrEnum):
    """How destination collisions are treated in the plan (v1: fail only)."""

    FAIL = "fail"


class PlanAction(StrEnum):
    COPY = "copy"
    MKDIR = "mkdir"
    LINK = "link"
    SKIP_IDENTICAL = "skip_identical"
    REFRESH_EPHEMERAL = "refresh_ephemeral"
    CONFLICT = "conflict"
    SKIP_EXCLUDED = "skip_excluded"


class EntryKind(StrEnum):
    FILE = "file"
    DIR = "dir"
    SYMLINK = "symlink"
    OTHER = "other"


@dataclass(frozen=True)
class PlannedEntry:
    relative_path: str
    kind: str
    action: str
    source_bytes: int = 0
    detail: str | None = None


@dataclass(frozen=True)
class MigrationPlanReport:
    """Observe-only migration plan from legacy → primary."""

    source_mount: str
    dest_mount: str
    conflict_policy: str
    migration_state: str
    active_write_role: str
    source_validation_ok: bool
    dest_validation_ok: bool
    source_blocker: str | None
    dest_blocker: str | None
    space: SpaceAssessment | None
    entries: tuple[PlannedEntry, ...] = ()
    copy_bytes: int = 0
    copy_file_count: int = 0
    mkdir_count: int = 0
    link_count: int = 0
    skip_identical_count: int = 0
    conflict_count: int = 0
    skip_excluded_count: int = 0
    refresh_ephemeral_count: int = 0
    refresh_ephemeral_bytes: int = 0
    conflict_bytes: int = 0
    source_file_count: int = 0
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def ready_for_migrate_execute(self) -> bool:
        """True when a future migrate-run could proceed (this command never copies)."""
        return not self.blockers and self.source_validation_ok and self.dest_validation_ok

    def summary_line(self) -> str:
        status = "READY (plan only)" if self.ready_for_migrate_execute else "BLOCKED"
        return (
            f"{status} · copy {self.copy_file_count} file(s) "
            f"({self.copy_bytes / (1024**3):.3f} GiB) · "
            f"{self.conflict_count} conflict(s) · "
            f"{self.refresh_ephemeral_count} ephemeral refresh · "
            f"{self.skip_identical_count} identical · "
            f"{self.mkdir_count} dir(s)"
        )

    def conflict_entries(self) -> tuple[PlannedEntry, ...]:
        return tuple(e for e in self.entries if e.action == PlanAction.CONFLICT.value)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.space is not None:
            payload["space"] = asdict(self.space)
        return payload


def entry_signature(path: Path) -> tuple[int, int]:
    st = path.lstat()
    return (int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))))



def is_ephemeral_relative(rel: str) -> bool:
    """True for live-append trees (logs/state) under the mount root."""
    if not rel or rel == ".":
        return False
    return Path(rel).parts[0] in EPHEMERAL_TOP_DIRS


def is_excluded_relative(rel: str) -> bool:
    """Exclude primary control namespace from migration equality / copy plan."""
    if not rel or rel == ".":
        return False
    parts = Path(rel).parts
    return parts[0] == CONTROL_DIRNAME


def iter_source_entries(source_root: Path) -> Iterable[tuple[str, Path, EntryKind]]:
    """Yield (relative_posix, absolute_path, kind) for all entries under source_root."""
    if not source_root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(source_root, topdown=True, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(source_root).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        dirnames[:] = [
            d
            for d in dirnames
            if not is_excluded_relative(f"{rel_dir}/{d}".lstrip("/"))
        ]

        if rel_dir and not is_excluded_relative(rel_dir):
            yield rel_dir, current, EntryKind.DIR

        for name in sorted(filenames):
            child = current / name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if is_excluded_relative(rel):
                yield rel, child, EntryKind.OTHER
                continue
            if child.is_symlink():
                yield rel, child, EntryKind.SYMLINK
            elif child.is_file():
                yield rel, child, EntryKind.FILE
            else:
                yield rel, child, EntryKind.OTHER


def _plan_entry(
    *,
    rel: str,
    source: Path,
    dest_root: Path,
    kind: EntryKind,
    conflict_policy: ConflictPolicy,
) -> PlannedEntry:
    if is_excluded_relative(rel):
        return PlannedEntry(
            relative_path=rel,
            kind=kind.value,
            action=PlanAction.SKIP_EXCLUDED.value,
            detail=f"excluded ({CONTROL_DIRNAME} is primary-only)",
        )

    dest = dest_root / rel

    if kind == EntryKind.DIR:
        if not dest.exists():
            return PlannedEntry(rel, kind.value, PlanAction.MKDIR.value)
        if dest.is_dir() and not dest.is_symlink():
            return PlannedEntry(
                rel, kind.value, PlanAction.SKIP_IDENTICAL.value, detail="directory exists"
            )
        return PlannedEntry(
            rel,
            kind.value,
            PlanAction.CONFLICT.value,
            detail=(
                "destination exists as non-directory "
                f"({'symlink' if dest.is_symlink() else 'file'})"
            ),
        )

    if kind == EntryKind.SYMLINK:
        target = os.readlink(source)
        if not dest.exists() and not dest.is_symlink():
            return PlannedEntry(
                rel, kind.value, PlanAction.LINK.value, 0, detail=f"-> {target}"
            )
        if dest.is_symlink():
            try:
                if os.readlink(dest) == target:
                    return PlannedEntry(
                        rel,
                        kind.value,
                        PlanAction.SKIP_IDENTICAL.value,
                        detail=f"-> {target}",
                    )
            except OSError:
                pass
        return PlannedEntry(
            rel,
            kind.value,
            PlanAction.CONFLICT.value,
            detail=f"symlink mismatch or destination occupied (source -> {target})",
        )

    try:
        size = int(source.lstat().st_size)
    except OSError as exc:
        return PlannedEntry(
            rel, kind.value, PlanAction.CONFLICT.value, detail=f"source unreadable: {exc}"
        )

    if not dest.exists() and not dest.is_symlink():
        return PlannedEntry(rel, kind.value, PlanAction.COPY.value, source_bytes=size)

    if dest.is_symlink() or dest.is_dir():
        return PlannedEntry(
            rel,
            kind.value,
            PlanAction.CONFLICT.value,
            source_bytes=size,
            detail="destination is directory or symlink",
        )

    if dest.is_file():
        try:
            if entry_signature(source) == entry_signature(dest):
                return PlannedEntry(
                    rel, kind.value, PlanAction.SKIP_IDENTICAL.value, source_bytes=size
                )
        except OSError as exc:
            return PlannedEntry(
                rel,
                kind.value,
                PlanAction.CONFLICT.value,
                source_bytes=size,
                detail=str(exc),
            )
        if is_ephemeral_relative(rel):
            return PlannedEntry(
                rel,
                kind.value,
                PlanAction.REFRESH_EPHEMERAL.value,
                source_bytes=size,
                detail="ephemeral tree (logs/state): refresh from legacy (not a hard conflict)",
            )
        return PlannedEntry(
            rel,
            kind.value,
            PlanAction.CONFLICT.value,
            source_bytes=size,
            detail="destination exists with different size or mtime (conflict_policy=fail)",
        )

    return PlannedEntry(
        rel,
        kind.value,
        PlanAction.CONFLICT.value,
        source_bytes=size,
        detail="unexpected destination type",
    )


def build_migration_plan(
    *,
    local_config: Path | None = None,
    conflict_policy: ConflictPolicy | str = ConflictPolicy.FAIL,
    config: StorageConfig | None = None,
) -> MigrationPlanReport:
    """
    Build a dry-run migration plan from legacy → primary.

    Does not copy, delete, remount, or edit config/fstab.
    """
    policy = ConflictPolicy(str(conflict_policy))
    if policy != ConflictPolicy.FAIL:
        raise ValueError("v1 migration planner only supports conflict_policy=fail")

    cfg = config or load_storage_config(local_config=local_config, warn_deprecated=False)
    source = cfg.legacy
    dest = cfg.primary

    source_val = validate_storage_mount(
        mount_path=source.mount_path,
        expected_uuid=source.filesystem_uuid,
        expected_fstype=source.filesystem_type,
        require_writable=False,
        space_policy=None,
    )
    dest_val = validate_storage_mount(
        mount_path=dest.mount_path,
        expected_uuid=dest.filesystem_uuid,
        expected_fstype=dest.filesystem_type,
        require_writable=True,
        space_policy=cfg.space_policy,
    )

    blockers: list[str] = []
    warnings: list[str] = []

    if cfg.cutover_complete:
        blockers.append("Cutover already complete — migration planner is for pre-cutover only.")
    if cfg.migration_state in {
        MigrationState.VERIFYING,
        MigrationState.VERIFIED,
        MigrationState.VERIFIED_PENDING_CUTOVER,
    }:
        warnings.append(
            f"migration_state={cfg.migration_state.value} (write freeze active for routine ops)."
        )
    if cfg.active_write_role.value != "legacy":
        warnings.append(
            (
                f"active_write_role={cfg.active_write_role.value} "
                "(post-cutover; planner is unavailable)."
                if cfg.cutover_complete
                else f"active_write_role={cfg.active_write_role.value} (expected legacy until cutover)."
            )
        )

    if not source_val.ok:
        blockers.append(f"Legacy source not ready: {source_val.blocker or source_val.code.value}")
    if not dest_val.ok:
        blockers.append(f"Primary destination not ready: {dest_val.blocker or dest_val.code.value}")

    if source_val.ok and (source.mount_path / CONTROL_DIRNAME).exists():
        warnings.append(
            f"Legacy contains {CONTROL_DIRNAME}/ — excluded from migration (primary-only namespace)."
        )

    entries: list[PlannedEntry] = []
    copy_bytes = 0
    counts = {
        PlanAction.COPY: 0,
        PlanAction.MKDIR: 0,
        PlanAction.LINK: 0,
        PlanAction.SKIP_IDENTICAL: 0,
        PlanAction.REFRESH_EPHEMERAL: 0,
        PlanAction.CONFLICT: 0,
        PlanAction.SKIP_EXCLUDED: 0,
    }
    source_file_count = 0
    refresh_bytes = 0
    conflict_bytes = 0

    if source_val.ok:
        seen_dirs: set[str] = set()
        for rel, path, kind in iter_source_entries(source.mount_path):
            if kind == EntryKind.DIR:
                if rel in seen_dirs:
                    continue
                seen_dirs.add(rel)
            if kind == EntryKind.FILE:
                source_file_count += 1
            planned = _plan_entry(
                rel=rel,
                source=path,
                dest_root=dest.mount_path,
                kind=kind,
                conflict_policy=policy,
            )
            entries.append(planned)
            action = PlanAction(planned.action)
            counts[action] = counts.get(action, 0) + 1
            if action == PlanAction.COPY:
                copy_bytes += planned.source_bytes
            elif action == PlanAction.REFRESH_EPHEMERAL:
                refresh_bytes += planned.source_bytes
            elif action == PlanAction.CONFLICT:
                conflict_bytes += planned.source_bytes

    space: SpaceAssessment | None = None
    estimated_bytes = copy_bytes + refresh_bytes + conflict_bytes
    if (
        dest_val.identity.capacity_bytes is not None
        and dest_val.identity.available_bytes is not None
    ):
        space = assess_space(
            cfg.space_policy,
            capacity_bytes=dest_val.identity.capacity_bytes,
            available_bytes=dest_val.identity.available_bytes,
            estimated_operation_bytes=estimated_bytes,
        )
        if not space.passes:
            blockers.append(
                f"Insufficient free space on primary for planned copy: {space.summary()}"
            )
    elif dest_val.ok:
        warnings.append("Could not assess primary free space.")

    if counts[PlanAction.CONFLICT] and policy == ConflictPolicy.FAIL:
        blockers.append(
            f"{counts[PlanAction.CONFLICT]} payload conflict(s) on primary "
            "(conflict_policy=fail; run migrate-quarantine or remove destination paths). "
            "Logs/state mismatches refresh automatically and are not conflicts."
        )

    if not entries and source_val.ok:
        warnings.append("Legacy mount is empty — nothing to migrate.")

    if counts[PlanAction.REFRESH_EPHEMERAL]:
        warnings.append(
            f"{counts[PlanAction.REFRESH_EPHEMERAL]} ephemeral path(s) will refresh from legacy "
            "(mercury_logs / mercury_state); not hard conflicts."
        )
    warnings.append(
        "Dry-run only: ./run.sh storage migrate-plan never copies. "
        "Next: ./run.sh storage migrate-run (preview) or migrate-quarantine if conflicts."
    )

    return MigrationPlanReport(
        source_mount=str(source.mount_path),
        dest_mount=str(dest.mount_path),
        conflict_policy=policy.value,
        migration_state=cfg.migration_state.value,
        active_write_role=cfg.active_write_role.value,
        source_validation_ok=source_val.ok,
        dest_validation_ok=dest_val.ok,
        source_blocker=source_val.blocker,
        dest_blocker=dest_val.blocker,
        space=space,
        entries=tuple(entries),
        copy_bytes=copy_bytes,
        copy_file_count=counts[PlanAction.COPY],
        mkdir_count=counts[PlanAction.MKDIR],
        link_count=counts[PlanAction.LINK],
        skip_identical_count=counts[PlanAction.SKIP_IDENTICAL],
        conflict_count=counts[PlanAction.CONFLICT],
        skip_excluded_count=counts[PlanAction.SKIP_EXCLUDED],
        refresh_ephemeral_count=counts[PlanAction.REFRESH_EPHEMERAL],
        refresh_ephemeral_bytes=refresh_bytes,
        conflict_bytes=conflict_bytes,
        source_file_count=source_file_count,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def default_migration_plan_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / "storage" / f"migration_plan_{stamp}.json"


def write_migration_plan_report(
    report: MigrationPlanReport, path: Path | None = None
) -> Path:
    """Write plan JSON under output/storage/ (repo artifact; not a volume write)."""
    target = path or default_migration_plan_report_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target

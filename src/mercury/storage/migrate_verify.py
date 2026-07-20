"""Independent verify of legacy → primary migration copy.

Compares all legacy content to primary (size + mtime). Primary ``.mercury_control``
is allowed as destination-only. Never copies, deletes, or switches writers.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.paths import LOCAL_CONFIG, OUTPUT_DIR
from mercury.core.storage_roles import CONTROL_DIRNAME, MigrationState
from mercury.core.storage_roots import StorageConfig, allowed_destination_only_paths, load_storage_config
from mercury.core.storage_validate import validate_storage_mount
from mercury.storage.migrate_plan import (
    EntryKind,
    entry_signature,
    is_ephemeral_relative,
    is_excluded_relative,
    iter_source_entries,
)
from mercury.storage.migrate_run import patch_migration_state


@dataclass(frozen=True)
class VerifyMismatch:
    relative_path: str
    issue: str
    detail: str | None = None


@dataclass(frozen=True)
class MigrationVerifyReport:
    source_mount: str
    dest_mount: str
    checked_files: int = 0
    checked_dirs: int = 0
    checked_links: int = 0
    matched: int = 0
    mismatches: tuple[VerifyMismatch, ...] = ()
    destination_only_allowed: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def ok(self) -> bool:
        return not self.blockers and not self.mismatches

    def summary_line(self) -> str:
        status = "VERIFIED" if self.ok else "FAILED"
        return (
            f"{status} · matched={self.matched} "
            f"mismatches={len(self.mismatches)} "
            f"checked_files={self.checked_files}"
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def verify_migration(
    *,
    local_config: Path | None = None,
    config: StorageConfig | None = None,
    update_state: bool = False,
    write_repo_report: bool = False,
) -> MigrationVerifyReport:
    """
    Verify legacy content is present and equal on primary.

    Equality uses size + mtime (same as migrate-plan). Does not switch writers.
    """
    cfg = config or load_storage_config(local_config=local_config, warn_deprecated=False)
    source = cfg.legacy
    dest = cfg.primary
    blockers: list[str] = []
    warnings: list[str] = []

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
        require_writable=False,
        space_policy=None,
    )
    if not source_val.ok:
        blockers.append(f"Legacy source not ready: {source_val.blocker or source_val.code.value}")
    if not dest_val.ok:
        blockers.append(f"Primary destination not ready: {dest_val.blocker or dest_val.code.value}")

    mismatches: list[VerifyMismatch] = []
    checked_files = checked_dirs = checked_links = matched = 0
    allowed_only = allowed_destination_only_paths()

    if source_val.ok and dest_val.ok:
        for rel, path, kind in iter_source_entries(source.mount_path):
            if is_excluded_relative(rel):
                continue
            target = dest.mount_path / rel
            if kind == EntryKind.DIR:
                checked_dirs += 1
                if target.is_dir() and not target.is_symlink():
                    matched += 1
                else:
                    mismatches.append(
                        VerifyMismatch(rel, "missing_or_not_dir", "expected directory on primary")
                    )
                continue
            if kind == EntryKind.SYMLINK:
                checked_links += 1
                if not target.is_symlink():
                    mismatches.append(
                        VerifyMismatch(rel, "missing_or_not_symlink", "expected symlink on primary")
                    )
                    continue
                try:
                    if os.readlink(path) == os.readlink(target):
                        matched += 1
                    else:
                        mismatches.append(
                            VerifyMismatch(rel, "symlink_target_mismatch", None)
                        )
                except OSError as exc:
                    mismatches.append(VerifyMismatch(rel, "symlink_unreadable", str(exc)))
                continue
            if kind == EntryKind.FILE:
                checked_files += 1
                if not target.is_file() or target.is_symlink():
                    mismatches.append(
                        VerifyMismatch(rel, "missing_or_not_file", "expected file on primary")
                    )
                    continue
                try:
                    if entry_signature(path) == entry_signature(target):
                        matched += 1
                    elif is_ephemeral_relative(rel):
                        # Logs/state drift while writers remain on legacy — presence is enough.
                        matched += 1
                        warnings.append(
                            f"ephemeral drift accepted: {rel}"
                        )
                    else:
                        mismatches.append(
                            VerifyMismatch(
                                rel,
                                "size_or_mtime_mismatch",
                                "destination differs (conflict_policy=fail equality)",
                            )
                        )
                except OSError as exc:
                    mismatches.append(VerifyMismatch(rel, "unreadable", str(exc)))

        if (dest.mount_path / CONTROL_DIRNAME).exists():
            warnings.append(f"{CONTROL_DIRNAME}/ present on primary (allowed destination-only).")

    if mismatches:
        blockers.append(
            f"{len(mismatches)} path mismatch(es) — re-run migrate-plan / migrate-run before cutover."
        )

    # Collapse repeated ephemeral-drift notes.
    ephemeral_notes = [w for w in warnings if w.startswith("ephemeral drift accepted:")]
    other_warnings = [w for w in warnings if not w.startswith("ephemeral drift accepted:")]
    if ephemeral_notes:
        ephemeral_context = (
            "archive drift after cutover; HDD is the active writer"
            if cfg.cutover_complete
            else "accepted until cutover; logs/state keep appending on legacy"
        )
        other_warnings.append(
            f"{len(ephemeral_notes)} ephemeral path(s) differ by size/mtime but are present "
            f"({ephemeral_context})."
        )
    warnings = other_warnings

    report = MigrationVerifyReport(
        source_mount=str(source.mount_path),
        dest_mount=str(dest.mount_path),
        checked_files=checked_files,
        checked_dirs=checked_dirs,
        checked_links=checked_links,
        matched=matched,
        mismatches=tuple(mismatches),
        destination_only_allowed=tuple(sorted(allowed_only)),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )

    if update_state:
        from dataclasses import replace

        notes: list[str] = []
        if report.ok:
            notes.extend(
                patch_migration_state(
                    MigrationState.VERIFIED, local_config=local_config or LOCAL_CONFIG
                )
            )
        report = replace(report, warnings=tuple(list(report.warnings) + notes))

    if write_repo_report:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = OUTPUT_DIR / "storage" / f"migration_verify_{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    return report

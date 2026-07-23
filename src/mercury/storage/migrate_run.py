"""Gated legacy → primary migration copy (dry-run default).

Does not switch ``active_write_role``, remount volumes, edit fstab, or delete
legacy content. Live copies require ``--execute`` and typing MIGRATE PRIMARY.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.paths import OUTPUT_DIR, resolve_local_config
from mercury.core.safety import MIGRATE_PRIMARY_CONFIRMATION_PHRASE
from mercury.core.storage_roles import CONTROL_DIRNAME, MigrationState, STORAGE_SCHEMA_VERSION
from mercury.core.storage_roots import StorageConfig, StorageIdentityDocument, load_storage_config
from mercury.storage.migrate_plan import PlanAction, PlannedEntry, build_migration_plan
from mercury.storage.progress_ledger import (
    append_progress,
    clear_ledger,
    completed_paths,
)


@dataclass(frozen=True)
class MigrationRunResult:
    """Outcome of migrate-run (preview or live copy)."""

    dry_run: bool
    executed: bool
    source_mount: str
    dest_mount: str
    plan_ready: bool
    copied_files: int = 0
    created_dirs: int = 0
    created_links: int = 0
    skipped_identical: int = 0
    refreshed_ephemeral: int = 0
    resumed_skipped: int = 0
    bytes_copied: int = 0
    errors: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    confirmation_phrase: str = MIGRATE_PRIMARY_CONFIRMATION_PHRASE
    control_report_path: str | None = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def ok(self) -> bool:
        return not self.blockers and not self.errors

    def summary_line(self) -> str:
        mode = "DRY-RUN" if self.dry_run else ("EXECUTED" if self.executed else "REFUSED")
        resume = (
            f" resume_skip={self.resumed_skipped}" if self.resumed_skipped else ""
        )
        return (
            f"{mode} · files={self.copied_files} dirs={self.created_dirs} "
            f"links={self.created_links} identical={self.skipped_identical} "
            f"ephemeral={self.refreshed_ephemeral}{resume} "
            f"bytes={self.bytes_copied}"
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _destination_occupied(dest: Path) -> bool:
    """True if dest exists as any filesystem entry, including a broken symlink."""
    return dest.exists() or dest.is_symlink()


def _atomic_copy_file(src: Path, dest: Path, *, overwrite: bool) -> None:
    """Copy via temp file + os.replace so interrupted copies do not leave short files."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _destination_occupied(dest) and not overwrite:
        raise FileExistsError(f"destination exists for copy: {dest}")
    tmp = dest.with_name(dest.name + ".mercury_migrate_tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        shutil.copy2(src, tmp, follow_symlinks=False)
        os.replace(tmp, dest)
    finally:
        if tmp.exists() or tmp.is_symlink():
            try:
                tmp.unlink()
            except OSError:
                pass


def _apply_entry(*, source_root: Path, dest_root: Path, entry: PlannedEntry) -> None:
    src = source_root / entry.relative_path
    dest = dest_root / entry.relative_path
    action = PlanAction(entry.action)
    if action == PlanAction.MKDIR:
        dest.mkdir(parents=True, exist_ok=True)
        return
    if action == PlanAction.LINK:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _destination_occupied(dest):
            raise FileExistsError(f"destination exists for link: {dest}")
        os.symlink(os.readlink(src), dest)
        return
    if action == PlanAction.COPY:
        _atomic_copy_file(src, dest, overwrite=False)
        return
    if action == PlanAction.REFRESH_EPHEMERAL:
        _atomic_copy_file(src, dest, overwrite=True)
        return
    raise ValueError(f"cannot apply action {entry.action} for {entry.relative_path}")


def _ensure_primary_control_dir(dest_root: Path) -> Path:
    control = dest_root / CONTROL_DIRNAME
    control.mkdir(parents=True, exist_ok=True)
    return control


def _write_control_migration_report(
    *,
    dest_root: Path,
    result: MigrationRunResult,
    config: StorageConfig,
) -> Path:
    control = _ensure_primary_control_dir(dest_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = control / f"migration_run_{stamp}.json"
    payload = {
        "schema_version": STORAGE_SCHEMA_VERSION,
        "result": result.to_dict(),
        "active_write_role": config.active_write_role.value,
        "migration_state_at_run": config.migration_state.value,
        "note": "Writers remain on legacy until cutover; this report is not a cutover.",
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    identity = control / "storage_identity.json"
    if not identity.exists():
        doc = StorageIdentityDocument(
            storage_role="canonical",
            filesystem_uuid=config.primary.filesystem_uuid,
            filesystem_label=config.primary.label,
            mount_path=str(config.primary.mount_path),
            filesystem_type=config.primary.filesystem_type,
            initialization_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        identity.write_text(
            doc.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    return path


def patch_migration_state(
    state: MigrationState | str,
    *,
    local_config: Path | None = None,
) -> list[str]:
    """Update ``[storage].migration_state`` in local.toml when present.

    Prefers an in-section replacement under ``[storage]`` (not nested tables).
    Verifies the value with tomllib after write.
    """
    import tomllib

    path = local_config or resolve_local_config()
    if not path.exists():
        return [f"{path}: missing — cannot update migration_state"]
    desired = MigrationState(str(state))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_storage = False
    replaced = False
    inserted = False
    saw_storage = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            table = stripped[1:-1].strip()
            if in_storage and not replaced and not inserted:
                out.append(f'migration_state = "{desired.value}"\n')
                inserted = True
            in_storage = table == "storage"
            if in_storage:
                saw_storage = True
            out.append(line)
            continue
        if in_storage and re.match(r"^\s*migration_state\s*=", line):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}migration_state = "{desired.value}"\n')
            replaced = True
            continue
        out.append(line)
    if in_storage and not replaced and not inserted:
        out.append(f'migration_state = "{desired.value}"\n')
        inserted = True
    if not saw_storage:
        return [
            f"{path}: no [storage] section — run config repair-local or add [storage] manually"
        ]
    new_text = "".join(out)
    if not new_text.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        got = (data.get("storage") or {}).get("migration_state")
    except Exception as exc:
        return [f"{path}: wrote migration_state but reload failed: {exc}"]
    if str(got) != desired.value:
        return [
            f"{path}: migration_state write did not stick "
            f"(wanted {desired.value!r}, got {got!r})"
        ]
    verb = "updated" if replaced else "inserted"
    return [f"{path}: {verb} migration_state = {desired.value!r}"]

def run_migration(
    *,
    execute: bool = False,
    confirmation: str | None = None,
    local_config: Path | None = None,
    config: StorageConfig | None = None,
    update_state: bool = True,
    write_repo_report: bool = False,
    progress_callback=None,
) -> MigrationRunResult:
    """
    Preview or execute legacy → primary copy.

    Default is dry-run. Live copy requires execute=True and confirmation phrase.
    Never switches writers or deletes legacy files.
    """
    cfg = config or load_storage_config(local_config=local_config, warn_deprecated=False)
    plan = build_migration_plan(local_config=local_config, config=cfg)
    blockers = list(plan.blockers)
    warnings = [
        w for w in plan.warnings if "migrate-plan never copies" not in w.lower()
    ]
    warnings.append("Routine writers remain on legacy until an explicit cutover command.")

    dry_run = not execute
    if execute:
        from mercury.storage.host_maintenance import writes_allowed

        if not writes_allowed():
            blockers.append(
                "Live migrate refused: host maintenance writes_allowed=false "
                "(Mercury HDD detach / destination rehearsal in progress)."
            )
        if (confirmation or "").strip() != MIGRATE_PRIMARY_CONFIRMATION_PHRASE:
            blockers.append(
                f"Live migrate requires typing {MIGRATE_PRIMARY_CONFIRMATION_PHRASE!r} "
                "(confirmation mismatch or missing)."
            )
        if not plan.ready_for_migrate_execute:
            blockers.append(
                "Migration plan is not ready — resolve blockers from migrate-plan first."
            )

    if blockers:
        return MigrationRunResult(
            dry_run=dry_run,
            executed=False,
            source_mount=plan.source_mount,
            dest_mount=plan.dest_mount,
            plan_ready=plan.ready_for_migrate_execute,
            skipped_identical=plan.skip_identical_count,
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(warnings),
        )

    source_root = Path(plan.source_mount)
    dest_root = Path(plan.dest_mount)

    if dry_run:
        already = completed_paths(dest_root)
        work_paths = {
            e.relative_path
            for e in plan.entries
            if e.action
            in {
                PlanAction.COPY.value,
                PlanAction.MKDIR.value,
                PlanAction.LINK.value,
                PlanAction.REFRESH_EPHEMERAL.value,
            }
        }
        resume_n = len(work_paths & already)
        dry_warnings = list(warnings)
        if resume_n:
            dry_warnings.append(
                f"Resume ledger would skip {resume_n} previously completed path(s) on --execute."
            )
        dry_warnings.append(
            "Dry-run only — no files copied. Re-run with --execute "
            f"and type {MIGRATE_PRIMARY_CONFIRMATION_PHRASE}."
        )
        return MigrationRunResult(
            dry_run=True,
            executed=False,
            source_mount=plan.source_mount,
            dest_mount=plan.dest_mount,
            plan_ready=True,
            copied_files=plan.copy_file_count,
            created_dirs=plan.mkdir_count,
            created_links=plan.link_count,
            skipped_identical=plan.skip_identical_count,
            refreshed_ephemeral=plan.refresh_ephemeral_count,
            resumed_skipped=resume_n,
            bytes_copied=plan.copy_bytes + plan.refresh_ephemeral_bytes,
            warnings=tuple(dry_warnings),
        )

    work = [
        e
        for e in plan.entries
        if e.action
        in {
            PlanAction.COPY.value,
            PlanAction.MKDIR.value,
            PlanAction.LINK.value,
            PlanAction.REFRESH_EPHEMERAL.value,
        }
    ]
    already_done = completed_paths(dest_root)
    pending = [e for e in work if e.relative_path not in already_done]
    resumed_skipped = len(work) - len(pending)
    if resumed_skipped:
        warnings.append(
            f"Resume ledger: skipping {resumed_skipped} previously completed path(s)."
        )

    if update_state:
        warnings.extend(
            patch_migration_state(
                MigrationState.COPYING, local_config=local_config or resolve_local_config()
            )
        )

    dirs = sorted(
        (e for e in pending if e.action == PlanAction.MKDIR.value),
        key=lambda e: e.relative_path.count("/"),
    )
    others = [e for e in pending if e.action != PlanAction.MKDIR.value]
    ordered = dirs + others
    errors: list[str] = []
    copied_files = created_dirs = created_links = refreshed = bytes_copied = 0
    total = len(ordered)

    for index, entry in enumerate(ordered, start=1):
        try:
            _apply_entry(source_root=source_root, dest_root=dest_root, entry=entry)
            append_progress(
                dest_root,
                relative_path=entry.relative_path,
                action=entry.action,
                status="ok",
                bytes_copied=entry.source_bytes,
            )
        except (OSError, ValueError) as exc:
            errors.append(f"{entry.relative_path}: {exc}")
            append_progress(
                dest_root,
                relative_path=entry.relative_path,
                action=entry.action,
                status="error",
                detail=str(exc),
            )
            break
        if entry.action == PlanAction.MKDIR.value:
            created_dirs += 1
        elif entry.action == PlanAction.LINK.value:
            created_links += 1
        elif entry.action == PlanAction.COPY.value:
            copied_files += 1
            bytes_copied += entry.source_bytes
        elif entry.action == PlanAction.REFRESH_EPHEMERAL.value:
            refreshed += 1
            bytes_copied += entry.source_bytes
        if progress_callback is not None:
            try:
                progress_callback(index, total, entry.relative_path, bytes_copied)
            except Exception:
                pass

    if errors:
        warnings.append(
            "Copy stopped early — progress ledger retained for resume. "
            "Fix the error, then re-run migrate-run --execute (completed paths are skipped)."
        )

    result = MigrationRunResult(
        dry_run=False,
        executed=not errors,
        source_mount=plan.source_mount,
        dest_mount=plan.dest_mount,
        plan_ready=True,
        copied_files=copied_files,
        created_dirs=created_dirs,
        created_links=created_links,
        skipped_identical=plan.skip_identical_count,
        refreshed_ephemeral=refreshed,
        resumed_skipped=resumed_skipped,
        bytes_copied=bytes_copied,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )

    if result.executed:
        try:
            clear_ledger(dest_root)
        except OSError:
            pass
        try:
            control_path = str(
                _write_control_migration_report(
                    dest_root=dest_root, result=result, config=cfg
                )
            )
            result = replace(result, control_report_path=control_path)
        except OSError as exc:
            result = replace(
                result,
                warnings=tuple(
                    list(result.warnings)
                    + [f"Could not write primary control report: {exc}"]
                ),
            )
        if update_state and not result.errors:
            notes = patch_migration_state(
                MigrationState.COPIED, local_config=local_config or resolve_local_config()
            )
            result = replace(result, warnings=tuple(list(result.warnings) + notes))

    if write_repo_report:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = OUTPUT_DIR / "storage" / f"migration_run_{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    return result

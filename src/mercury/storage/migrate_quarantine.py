"""Quarantine conflicting primary paths so migrate-run can proceed (fail policy).

Moves destination conflict paths under ``{primary}/.mercury_control/quarantine/``.
Never deletes legacy USB content and never overwrites in place.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.storage_roles import CONTROL_DIRNAME
from mercury.storage.migrate_plan import is_excluded_relative
from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.storage.migrate_plan import PlanAction, build_migration_plan

QUARANTINE_CONFIRMATION_PHRASE = "QUARANTINE CONFLICTS"

def _safe_primary_path(dest_root: Path, rel: str) -> Path:
    """Resolve rel under dest_root; raise ValueError on traversal / control paths."""
    if not rel or rel.strip() == ".":
        raise ValueError("empty relative path")
    path = Path(rel)
    if path.is_absolute():
        raise ValueError(f"absolute path refused: {rel}")
    if ".." in path.parts:
        raise ValueError(f"path traversal refused: {rel}")
    if is_excluded_relative(rel) or path.parts[0] == CONTROL_DIRNAME:
        raise ValueError(f"control namespace refused: {rel}")
    root = dest_root.resolve()
    candidate = (dest_root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes primary root: {rel}") from exc
    return candidate




@dataclass(frozen=True)
class QuarantineResult:
    dry_run: bool
    executed: bool
    source_mount: str
    dest_mount: str
    quarantined: tuple[str, ...] = ()
    quarantine_root: str | None = None
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    confirmation_phrase: str = QUARANTINE_CONFIRMATION_PHRASE
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def ok(self) -> bool:
        return not self.blockers

    def summary_line(self) -> str:
        mode = "DRY-RUN" if self.dry_run else ("EXECUTED" if self.executed else "REFUSED")
        return f"{mode} · quarantined={len(self.quarantined)}"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def quarantine_migration_conflicts(
    *,
    execute: bool = False,
    confirmation: str | None = None,
    local_config: Path | None = None,
    config: StorageConfig | None = None,
) -> QuarantineResult:
    """
    Move primary conflict paths into ``.mercury_control/quarantine/``.

    Default dry-run. Live move requires confirmation phrase.
    """
    cfg = config or load_storage_config(local_config=local_config, warn_deprecated=False)
    plan = build_migration_plan(local_config=local_config, config=cfg)
    conflicts = [e for e in plan.entries if e.action == PlanAction.CONFLICT.value]
    warnings = [
        "Quarantine only moves primary paths; legacy USB is untouched.",
        "After quarantine, re-run ./run.sh storage migrate-plan then migrate-run.",
    ]
    blockers: list[str] = []

    if cfg.cutover_complete:
        blockers.append("Cutover already complete — quarantine is for pre-cutover only.")
    if not plan.source_validation_ok:
        blockers.append(plan.source_blocker or "Legacy source not ready")
    if not plan.dest_validation_ok:
        blockers.append(plan.dest_blocker or "Primary destination not ready")
    if not conflicts:
        warnings.append("No conflicts to quarantine.")

    dry_run = not execute
    if execute:
        if (confirmation or "").strip() != QUARANTINE_CONFIRMATION_PHRASE:
            blockers.append(
                f"Live quarantine requires typing {QUARANTINE_CONFIRMATION_PHRASE!r}."
            )

    paths = tuple(e.relative_path for e in conflicts)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    qroot = Path(plan.dest_mount) / CONTROL_DIRNAME / "quarantine" / stamp

    if blockers:
        return QuarantineResult(
            dry_run=dry_run,
            executed=False,
            source_mount=plan.source_mount,
            dest_mount=plan.dest_mount,
            quarantined=paths,
            quarantine_root=str(qroot),
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    if dry_run:
        return QuarantineResult(
            dry_run=True,
            executed=False,
            source_mount=plan.source_mount,
            dest_mount=plan.dest_mount,
            quarantined=paths,
            quarantine_root=str(qroot),
            warnings=tuple(
                warnings
                + [
                    "Dry-run only — nothing moved. Re-run with --execute "
                    f"and type {QUARANTINE_CONFIRMATION_PHRASE}."
                ]
            ),
        )

    if not paths:
        return QuarantineResult(
            dry_run=False,
            executed=False,
            source_mount=plan.source_mount,
            dest_mount=plan.dest_mount,
            quarantined=(),
            quarantine_root=str(qroot),
            warnings=tuple(warnings),
        )

    moved: list[str] = []
    errors: list[str] = []
    dest_root = Path(plan.dest_mount)
    qroot_resolved = qroot.resolve()
    # Ensure quarantine root stays under primary control namespace.
    try:
        qroot_resolved.relative_to((dest_root / CONTROL_DIRNAME).resolve())
    except ValueError:
        return QuarantineResult(
            dry_run=False,
            executed=False,
            source_mount=plan.source_mount,
            dest_mount=plan.dest_mount,
            quarantined=(),
            quarantine_root=str(qroot),
            blockers=("quarantine root escaped primary control namespace",),
            warnings=tuple(warnings),
        )
    for rel in paths:
        try:
            src = _safe_primary_path(dest_root, rel)
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            break
        if not src.exists() and not src.is_symlink():
            continue
        target = qroot / Path(rel)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target_resolved = target.resolve()
            target_resolved.relative_to(qroot_resolved)
            shutil.move(str(src), str(target))
            moved.append(rel)
        except (OSError, ValueError) as exc:
            errors.append(f"{rel}: {exc}")
            break

    blockers_out = tuple(errors)
    return QuarantineResult(
        dry_run=False,
        executed=not errors and bool(moved),
        source_mount=plan.source_mount,
        dest_mount=plan.dest_mount,
        quarantined=tuple(moved),
        quarantine_root=str(qroot),
        blockers=blockers_out,
        warnings=tuple(warnings),
    )

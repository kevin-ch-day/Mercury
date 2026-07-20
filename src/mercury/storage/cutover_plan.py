"""Read-only writer-cutover plan; execution is intentionally not implemented."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.storage.cutover_readiness import CutoverReadinessReport, build_cutover_readiness


@dataclass(frozen=True)
class CutoverPathChange:
    key: str
    legacy_path: str
    primary_path: str


@dataclass(frozen=True)
class CutoverPlan:
    """Every configuration change a future writer cutover must make together."""

    readiness: CutoverReadinessReport
    path_changes: tuple[CutoverPathChange, ...]
    target_active_write_role: str = "primary"
    execution_available: bool = False
    already_complete: bool = False
    runtime_blockers: tuple[str, ...] = (
        "No atomic cutover transaction updates the storage role and all legacy [mercury] paths together.",
        "Mercury does not yet validate a post-cutover writer, retain a rollback record, or lock the USB archive read-only.",
    )

    @property
    def ready_for_future_execution(self) -> bool:
        return self.readiness.ready and self.execution_available and not self.runtime_blockers

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_cutover_plan(
    *, local_config: Path | None = None, config: StorageConfig | None = None
) -> CutoverPlan:
    """Plan, but never apply, the complete coordinated writer-path change."""
    cfg = config or load_storage_config(local_config=local_config, warn_deprecated=False)
    readiness = build_cutover_readiness(local_config=local_config, config=cfg)
    legacy = {
        "backup_root": str(cfg.legacy.backup_root),
        "log_dir": str(cfg.legacy.log_dir),
        "repo_backup_root": str(cfg.legacy.repo_backup_root),
        "manifest_dir": str(cfg.legacy.manifest_dir),
        "runbook_dir": str(cfg.legacy.runbook_dir),
    }
    primary = {
        "backup_root": str(cfg.primary.backup_root),
        "log_dir": str(cfg.primary.log_dir),
        "repo_backup_root": str(cfg.primary.repo_backup_root),
        "manifest_dir": str(cfg.primary.manifest_dir),
        "runbook_dir": str(cfg.primary.runbook_dir),
    }
    keys = ("backup_root", "log_dir", "repo_backup_root", "manifest_dir", "runbook_dir")
    return CutoverPlan(
        readiness=readiness,
        path_changes=tuple(CutoverPathChange(key, legacy[key], primary[key]) for key in keys),
        already_complete=cfg.cutover_complete,
        runtime_blockers=() if cfg.cutover_complete else CutoverPlan.runtime_blockers,
    )

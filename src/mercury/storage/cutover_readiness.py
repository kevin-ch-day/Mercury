"""Read-only cutover readiness checklist (does not switch writers)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.storage_roles import CONTROL_DIRNAME, MigrationState
from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.storage.migrate_plan import build_migration_plan
from mercury.storage.report import suggested_primary_fstab_line


@dataclass(frozen=True)
class CutoverCheck:
    key: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class CutoverReadinessReport:
    ready: bool
    active_write_role: str
    migration_state: str
    checks: tuple[CutoverCheck, ...]
    fstab_draft: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_cutover_readiness(
    *,
    local_config: Path | None = None,
    config: StorageConfig | None = None,
) -> CutoverReadinessReport:
    """
    Checklist for future cutover. Never remounts, never switches writers.
    """
    cfg = config or load_storage_config(local_config=local_config, warn_deprecated=False)
    plan = build_migration_plan(local_config=local_config, config=cfg)
    checks: list[CutoverCheck] = []
    warnings: list[str] = []

    checks.append(
        CutoverCheck(
            "legacy_mount",
            plan.source_validation_ok,
            plan.source_blocker or "legacy mounted",
        )
    )
    checks.append(
        CutoverCheck(
            "primary_mount",
            plan.dest_validation_ok,
            plan.dest_blocker or "primary mounted and writable",
        )
    )
    checks.append(
        CutoverCheck(
            "active_writer_legacy",
            cfg.active_write_role.value == "legacy",
            f"active_write_role={cfg.active_write_role.value} (must be legacy until cutover)",
        )
    )
    state_ok = cfg.migration_state in {
        MigrationState.VERIFIED,
        MigrationState.VERIFIED_PENDING_CUTOVER,
    }
    checks.append(
        CutoverCheck(
            "migration_verified",
            state_ok,
            f"migration_state={cfg.migration_state.value} (need verified before cutover)",
        )
    )
    checks.append(
        CutoverCheck(
            "no_payload_conflicts",
            plan.conflict_count == 0,
            f"{plan.conflict_count} payload conflict(s)",
        )
    )
    identity = cfg.primary.mount_path / CONTROL_DIRNAME / "storage_identity.json"
    checks.append(
        CutoverCheck(
            "control_identity",
            identity.is_file(),
            str(identity) if identity.is_file() else "missing .mercury_control/storage_identity.json",
        )
    )
    space_ok = plan.space is None or plan.space.passes
    checks.append(
        CutoverCheck(
            "primary_space",
            space_ok,
            plan.space.summary() if plan.space else "space not assessed",
        )
    )

    blockers = [c.detail for c in checks if not c.ok]
    if cfg.cutover_complete:
        warnings.append("cutover_complete already true — readiness is informational.")
    warnings.append(
        "Cutover approve that switches writers / remounts legacy RO is not enabled yet."
    )
    warnings.append(f"Suggested fstab draft (not applied): {suggested_primary_fstab_line(cfg)}")

    ready = all(c.ok for c in checks) and not cfg.cutover_complete
    return CutoverReadinessReport(
        ready=ready,
        active_write_role=cfg.active_write_role.value,
        migration_state=cfg.migration_state.value,
        checks=tuple(checks),
        fstab_draft=suggested_primary_fstab_line(cfg),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )

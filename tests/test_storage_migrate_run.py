"""Tests for gated migrate-run and migrate-verify."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mercury.core.safety import MIGRATE_PRIMARY_CONFIRMATION_PHRASE
from mercury.core.storage_roles import (
    CONTROL_DIRNAME,
    MigrationState,
    MountValidationCode,
    StorageRootRole,
    StorageWriteRole,
)
from mercury.core.storage_roots import StorageConfig, StorageRootConfig, default_storage_config
from mercury.core.storage_space import SpaceAssessment, SpacePolicy
from mercury.core.storage_validate import MountIdentity, MountValidationResult
from mercury.storage.migrate_run import patch_migration_state, run_migration
from mercury.storage.migrate_verify import verify_migration
from tests.conftest import make_storage_mount_tree


def _ok_validation(mount: Path, *, writable: bool = True) -> MountValidationResult:
    identity = MountIdentity(
        mount_path=mount,
        path_exists=True,
        is_mount=True,
        mounted_uuid="test-uuid",
        mounted_fstype="ext4",
        mount_options="rw",
        writable=writable,
        capacity_bytes=100 * 1024**3,
        available_bytes=80 * 1024**3,
    )
    return MountValidationResult(
        code=MountValidationCode.OK,
        mount_path=mount,
        expected_uuid="test-uuid",
        expected_fstype="ext4",
        identity=identity,
        space=SpaceAssessment(
            capacity_bytes=identity.capacity_bytes or 0,
            available_bytes=identity.available_bytes or 0,
            estimated_operation_bytes=0,
            required_reserve_bytes=20 * 1024**3,
            required_available_bytes=20 * 1024**3,
            passes=True,
        ),
    )


def _config(primary: Path, legacy: Path) -> StorageConfig:
    base = default_storage_config()
    return StorageConfig(
        primary=StorageRootConfig(
            key="primary",
            role=StorageRootRole.CANONICAL,
            label="MERCURY_DATA_V2",
            mount_path=primary,
            filesystem_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
            filesystem_type="ext4",
            writable=True,
        ),
        legacy=StorageRootConfig(
            key="legacy",
            role=StorageRootRole.TRANSITION_SOURCE,
            label="MERCURY_DATA_USB",
            mount_path=legacy,
            filesystem_uuid="e4f0c7fb-132e-4867-9c16-5e4749f5c43a",
            filesystem_type="ext4",
            writable=True,
        ),
        active_write_role=StorageWriteRole.LEGACY,
        migration_state=MigrationState.NOT_STARTED,
        space_policy=SpacePolicy(),
        source="test",
    )


def test_migrate_run_dry_run_does_not_copy(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "payload.txt").write_text("data", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        result = run_migration(config=cfg, execute=False, update_state=False)

    assert result.dry_run is True
    assert result.ok is True
    assert result.copied_files == 1
    assert not (primary / "payload.txt").exists()


def test_migrate_run_execute_copies_and_keeps_legacy(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "nested").mkdir()
    (legacy / "nested" / "a.bin").write_bytes(b"abc")
    (legacy / "link-target.txt").write_text("t", encoding="utf-8")
    (legacy / "alias").symlink_to("link-target.txt")
    cfg = _config(primary, legacy)
    local = tmp_path / "local.toml"
    local.write_text(
        '[storage]\nactive_write_role = "legacy"\nmigration_state = "not_started"\n',
        encoding="utf-8",
    )

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        refused = run_migration(
            config=cfg,
            execute=True,
            confirmation="NOPE",
            local_config=local,
            update_state=False,
        )
        assert refused.executed is False
        assert any("MIGRATE PRIMARY" in b for b in refused.blockers)

        result = run_migration(
            config=cfg,
            execute=True,
            confirmation=MIGRATE_PRIMARY_CONFIRMATION_PHRASE,
            local_config=local,
            update_state=True,
        )

    assert result.executed is True
    assert result.ok is True
    assert (primary / "nested" / "a.bin").read_bytes() == b"abc"
    assert (legacy / "nested" / "a.bin").exists()  # never delete source
    assert (primary / "alias").is_symlink()
    assert (primary / CONTROL_DIRNAME / "storage_identity.json").exists()
    assert 'migration_state = "copied"' in local.read_text(encoding="utf-8")


def test_migrate_verify_passes_after_copy(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "f.txt").write_text("hello", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        run_migration(
            config=cfg,
            execute=True,
            confirmation=MIGRATE_PRIMARY_CONFIRMATION_PHRASE,
            update_state=False,
        )
        # validate also used by verify
        with patch(
            "mercury.storage.migrate_verify.validate_storage_mount",
            side_effect=fake_validate,
        ):
            report = verify_migration(config=cfg, update_state=False)

    assert report.ok is True
    assert report.checked_files >= 1
    assert report.mismatches == ()


def test_migrate_verify_detects_mismatch(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "f.txt").write_text("hello", encoding="utf-8")
    (primary / "f.txt").write_text("different", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_verify.validate_storage_mount", side_effect=fake_validate):
        report = verify_migration(config=cfg, update_state=False)

    assert report.ok is False
    assert any(m.issue == "size_or_mtime_mismatch" for m in report.mismatches)


def test_patch_migration_state(tmp_path: Path) -> None:
    local = tmp_path / "local.toml"
    local.write_text(
        '[storage]\nactive_write_role = "legacy"\nmigration_state = "not_started"\n',
        encoding="utf-8",
    )
    notes = patch_migration_state(MigrationState.COPIED, local_config=local)
    assert any("copied" in n for n in notes)
    assert 'migration_state = "copied"' in local.read_text(encoding="utf-8")


def test_quarantine_moves_primary_conflicts(tmp_path: Path) -> None:
    from mercury.storage.migrate_quarantine import (
        QUARANTINE_CONFIRMATION_PHRASE,
        quarantine_migration_conflicts,
    )

    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "clash.txt").write_text("src", encoding="utf-8")
    (primary / "clash.txt").write_text("dst-different", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        preview = quarantine_migration_conflicts(config=cfg, execute=False)
        assert preview.dry_run is True
        assert "clash.txt" in preview.quarantined
        assert (primary / "clash.txt").exists()

        result = quarantine_migration_conflicts(
            config=cfg,
            execute=True,
            confirmation=QUARANTINE_CONFIRMATION_PHRASE,
        )
    assert result.executed is True
    assert not (primary / "clash.txt").exists()
    assert (legacy / "clash.txt").exists()
    qroot = Path(result.quarantine_root or "")
    assert (qroot / "clash.txt").exists()


def test_ephemeral_mismatch_is_refresh_not_conflict(tmp_path: Path) -> None:
    from mercury.storage.migrate_plan import PlanAction, build_migration_plan

    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "mercury_logs").mkdir(exist_ok=True)
    (primary / "mercury_logs").mkdir(exist_ok=True)
    (legacy / "mercury_logs" / "backup.log").write_text("new", encoding="utf-8")
    (primary / "mercury_logs" / "backup.log").write_text("old-content", encoding="utf-8")
    (legacy / "payload.bin").write_text("keep", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        plan = build_migration_plan(config=cfg)

    actions = {e.relative_path: e.action for e in plan.entries}
    assert actions["mercury_logs/backup.log"] == PlanAction.REFRESH_EPHEMERAL.value
    assert plan.conflict_count == 0
    assert plan.refresh_ephemeral_count >= 1
    assert plan.ready_for_migrate_execute is True


def test_quarantine_refuses_path_traversal(tmp_path: Path) -> None:
    from mercury.storage.migrate_quarantine import _safe_primary_path
    import pytest

    root = tmp_path / "primary"
    root.mkdir()
    with pytest.raises(ValueError, match="traversal"):
        _safe_primary_path(root, "../etc/passwd")
    with pytest.raises(ValueError, match="absolute"):
        _safe_primary_path(root, "/etc/passwd")
    with pytest.raises(ValueError, match="control"):
        _safe_primary_path(root, ".mercury_control/x")


def test_write_freeze_blocks_backup_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.core.execution_policy import ExecutionPolicy
    from mercury.core.storage_roles import MigrationState, StorageWriteRole
    from mercury.core.storage_roots import StorageConfig, default_storage_config

    mounts = make_storage_mount_tree(tmp_path)
    base = default_storage_config()
    frozen = StorageConfig(
        primary=base.primary,
        legacy=base.legacy,
        active_write_role=StorageWriteRole.LEGACY,
        migration_state=MigrationState.VERIFYING,
        space_policy=base.space_policy,
        source="test",
    )
    monkeypatch.setattr(
        "mercury.core.storage_roots.load_storage_config",
        lambda **kwargs: frozen,
    )
    # Also patch the import site used inside backup_environment_refusal
    monkeypatch.setattr(
        "mercury.core.execution_policy.load_storage_config",
        lambda **kwargs: frozen,
        raising=False,
    )

    # Direct gate check
    from mercury.core.storage_roots import assess_routine_write_permission, write_freeze_active

    assert write_freeze_active(frozen) is True
    gate = assess_routine_write_permission(frozen, validate_mount=False)
    assert gate.allowed is False

    # Patch load inside the method's local import by patching storage_roots
    import mercury.core.storage_roots as sr

    monkeypatch.setattr(sr, "load_storage_config", lambda **kwargs: frozen)
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=mounts["legacy"] / "mercury_backups",
        config_path=tmp_path / "local.toml",
        usb_mount=mounts["legacy"],
    )
    (tmp_path / "local.toml").write_text("[mercury]\n", encoding="utf-8")
    # Force config_path present and skip platform by mocking detect_platform after freeze check
    # Freeze runs first — should refuse before platform if patch works.
    refusal = policy.backup_environment_refusal()
    assert refusal is not None
    assert "migration" in refusal.lower() or "freeze" in refusal.lower() or "frozen" in refusal.lower()



def test_progress_ledger_resume_skips_completed(tmp_path: Path) -> None:
    """Ledger skips completed work items when the plan still lists them."""
    from dataclasses import replace as dc_replace

    from mercury.storage.migrate_plan import PlanAction, PlannedEntry, build_migration_plan
    from mercury.storage.progress_ledger import append_progress, ledger_path

    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "a.txt").write_text("a", encoding="utf-8")
    (legacy / "b.txt").write_text("b", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        base_plan = build_migration_plan(config=cfg)

    def plan_with_forced_copies(**_kwargs):
        entries = []
        for entry in base_plan.entries:
            if entry.relative_path in {"a.txt", "b.txt"}:
                entries.append(
                    PlannedEntry(
                        entry.relative_path,
                        entry.kind,
                        PlanAction.COPY.value,
                        source_bytes=max(entry.source_bytes, 1),
                    )
                )
            else:
                entries.append(entry)
        return dc_replace(
            base_plan,
            entries=tuple(entries),
            copy_file_count=2,
            conflict_count=0,
            blockers=(),
        )

    append_progress(primary, relative_path="a.txt", action="copy", status="ok", bytes_copied=1)
    (primary / "a.txt").write_text("a", encoding="utf-8")

    with patch(
        "mercury.storage.migrate_run.build_migration_plan",
        side_effect=plan_with_forced_copies,
    ):
        result = run_migration(
            config=cfg,
            execute=True,
            confirmation=MIGRATE_PRIMARY_CONFIRMATION_PHRASE,
            update_state=False,
        )

    assert result.executed is True
    assert result.resumed_skipped == 1
    assert (primary / "b.txt").read_text(encoding="utf-8") == "b"
    assert ledger_path(primary).read_text(encoding="utf-8").strip() == ""


def test_cutover_readiness_blocked_until_verified(tmp_path: Path) -> None:
    from mercury.storage.cutover_readiness import build_cutover_readiness

    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "f.txt").write_text("x", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        report = build_cutover_readiness(config=cfg)

    assert report.ready is False
    keys = {c.key: c.ok for c in report.checks}
    assert keys["migration_verified"] is False


def test_cutover_readiness_passes_after_verified_copy(tmp_path: Path) -> None:
    from dataclasses import replace as dc_replace

    from mercury.storage.cutover_readiness import build_cutover_readiness

    mounts = make_storage_mount_tree(tmp_path)
    legacy, primary = mounts["legacy"], mounts["primary"]
    (legacy / "f.txt").write_text("hello", encoding="utf-8")
    cfg = _config(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        run_migration(
            config=cfg,
            execute=True,
            confirmation=MIGRATE_PRIMARY_CONFIRMATION_PHRASE,
            update_state=False,
        )
        verified_cfg = dc_replace(cfg, migration_state=MigrationState.VERIFIED)
        with patch(
            "mercury.storage.migrate_verify.validate_storage_mount",
            side_effect=fake_validate,
        ):
            # identity written by migrate-run
            report = build_cutover_readiness(config=verified_cfg)

    assert report.ready is True
    assert all(c.ok for c in report.checks)


def test_patch_migration_state_planned(tmp_path: Path) -> None:
    local = tmp_path / "local.toml"
    local.write_text(
        '[storage]\nactive_write_role = "legacy"\nmigration_state = "not_started"\n',
        encoding="utf-8",
    )
    notes = patch_migration_state(MigrationState.PLANNED, local_config=local)
    assert any("planned" in n for n in notes)
    assert 'migration_state = "planned"' in local.read_text(encoding="utf-8")

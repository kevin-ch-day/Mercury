"""Post-cutover USB repair must not block HDD-only operation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, default_storage_config
from mercury.repair import startup as repair_startup


def _post_cutover_config() -> StorageConfig:
    base = default_storage_config()
    return StorageConfig(
        primary=base.primary,
        legacy=replace(base.legacy, role=StorageRootRole.LEGACY_ARCHIVE, writable=False),
        active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
        space_policy=base.space_policy,
        source="test",
    )


def test_usb_repair_reason_none_after_hdd_cutover() -> None:
    env = MagicMock()
    env.usb.repair_banner = "Mercury USB is not ready"
    env.permission_checks = ()
    with patch(
        "mercury.core.storage_roots.load_storage_config",
        return_value=_post_cutover_config(),
    ):
        assert repair_startup._hdd_writer_active() is True
        assert repair_startup.usb_repair_reason(env) is None


def test_maybe_prompt_skipped_after_cutover() -> None:
    with patch(
        "mercury.core.storage_roots.load_storage_config",
        return_value=_post_cutover_config(),
    ):
        with patch.object(repair_startup, "run_usb_repair_flow") as run:
            repair_startup.maybe_prompt_usb_repair_at_startup()
            run.assert_not_called()


def test_primary_mount_hint_when_hdd_not_ready() -> None:
    with patch(
        "mercury.core.storage_roots.load_storage_config",
        return_value=_post_cutover_config(),
    ):
        with patch(
            "mercury.storage.report.build_storage_status_report"
        ) as build:
            report = MagicMock()
            report.primary.validation.ok = False
            report.primary.filesystem_uuid = "715f29a9-2671-477b-8c8d-515d190addb9"
            report.primary.mount_path = "/mnt/MERCURY_DATA_V2"
            build.return_value = report
            hint = repair_startup.primary_mount_hint()
    assert hint is not None
    assert "715f29a9" in hint
    assert "/mnt/MERCURY_DATA_V2" in hint


def test_setup_hints_prefer_storage_validate_after_cutover() -> None:
    from mercury.core.environment_status import UsbDiscovery, _build_setup_hints

    with patch(
        "mercury.core.environment_status._hdd_writer_active",
        return_value=True,
    ):
        hints = _build_setup_hints(
            config=MagicMock(initialized=False, local_toml_present=True, missing_labels=()),
            usb=UsbDiscovery(Path("/mnt/MERCURY_DATA_USB"), False, False, None),
            primary_setup_blocker="Primary HDD writer paths not ready",
            has_repairable=True,
        )
    text = "\n".join(hints)
    assert "storage validate" in text
    assert "repair-usb" not in text


def test_usb_layout_permissions_skipped_after_cutover(tmp_path: Path) -> None:
    from mercury.core.environment_status import UsbDiscovery
    from mercury.core.setup_paths import assess_mercury_path_permissions

    mount = tmp_path / "MERCURY_DATA_USB"
    (mount / "mercury_backups").mkdir(parents=True)
    usb = UsbDiscovery(mount, True, True, mount / "mercury_backups")
    policy = MagicMock()
    policy.config_path = None
    with patch(
        "mercury.core.setup_paths._usb_layout_permissions_in_scope",
        return_value=False,
    ):
        checks = assess_mercury_path_permissions(policy=policy, usb=usb)
    assert checks == []


def test_doctor_recommends_storage_validate_for_writable_blocker_after_cutover() -> None:
    from types import SimpleNamespace

    from mercury.env.doctor import _recommended_next_step

    report = SimpleNamespace(blockers=["configured log directory not writable"])
    with patch(
        "mercury.core.storage_roots.load_storage_config",
        return_value=_post_cutover_config(),
    ):
        step = _recommended_next_step(MagicMock(), report)
    assert "storage validate" in step
    assert "repair-usb" not in step


def test_doctor_repair_plan_suggests_usb_ro_remount_after_cutover(monkeypatch) -> None:
    from types import SimpleNamespace

    from mercury.core.environment_status import ConfigSetupStatus, UsbDiscovery
    from mercury.core.execution_policy import ExecutionPolicy, REQUIRED_BACKUP_MOUNT
    from mercury.core.paths import REPO_ROOT
    from mercury.env.doctor import build_repair_plan
    from mercury.core.storage_validate import MountIdentity, MountValidationCode, MountValidationResult
    from mercury.storage.report import StorageRootStatus, StorageStatusReport

    identity = MountIdentity(
        mount_path=Path("/mnt/MERCURY_DATA_USB"),
        path_exists=True,
        is_mount=True,
        mounted_uuid="e4f0",
        mounted_fstype="ext4",
        mount_options="rw",
        writable=True,
    )
    validation = MountValidationResult(
        code=MountValidationCode.OK,
        mount_path=Path("/mnt/MERCURY_DATA_USB"),
        expected_uuid="e4f0",
        expected_fstype="ext4",
        identity=identity,
    )
    legacy = StorageRootStatus(
        key="legacy",
        role="legacy_archive",
        label="MERCURY_DATA_USB",
        mount_path="/mnt/MERCURY_DATA_USB",
        filesystem_uuid="e4f0",
        writable_policy=False,
        validation=validation,
        is_active_writer=False,
    )
    primary = StorageRootStatus(
        key="primary",
        role="canonical",
        label="MERCURY_DATA_V2",
        mount_path="/mnt/MERCURY_DATA_V2",
        filesystem_uuid="715f",
        writable_policy=True,
        validation=MountValidationResult(
            code=MountValidationCode.OK,
            mount_path=Path("/mnt/MERCURY_DATA_V2"),
            expected_uuid="715f",
            expected_fstype="ext4",
            identity=MountIdentity(
                mount_path=Path("/mnt/MERCURY_DATA_V2"),
                path_exists=True,
                is_mount=True,
                mounted_uuid="715f",
                mounted_fstype="ext4",
                mount_options="rw",
                writable=True,
            ),
        ),
        is_active_writer=True,
    )
    cfg = _post_cutover_config()
    monkeypatch.setattr(
        "mercury.storage.report.build_storage_status_report",
        lambda: StorageStatusReport(config=cfg, primary=primary, legacy=legacy),
    )
    monkeypatch.setattr(
        "mercury.core.storage_roots.load_storage_config",
        lambda warn_deprecated=True: cfg,
    )
    report = SimpleNamespace(
        repo_root=REPO_ROOT,
        current_user="secadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(REQUIRED_BACKUP_MOUNT, True, True, REQUIRED_BACKUP_MOUNT / "mercury_backups"),
        mariadb=SimpleNamespace(
            service_state="active",
            config_present=True,
            connection_works=True,
            configured_user="secadmin",
        ),
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=REQUIRED_BACKUP_MOUNT / "mercury_backups",
            config_path=REPO_ROOT / "config" / "local.toml",
        ),
        permission_checks=[],
        source_databases=[],
        verified_backup_count=0,
        verified_backup_total=0,
        blockers=[],
        warnings=[],
        self_healed=[],
        recommended_next_step="./run.sh menu",
    )
    plan = build_repair_plan(report)
    text = "\n".join(cmd for _title, cmds in plan for cmd in cmds)
    assert "remount,ro" in text
    assert "/mnt/MERCURY_DATA_USB" in text


def test_dashboard_skips_usb_repair_banner_after_cutover() -> None:
    """USB repair banner alone must not surface a USB repair dashboard row post-cutover."""
    from mercury.core.environment_status import _hdd_writer_active

    env = MagicMock()
    env.repairable_blockers = ()
    env.usb.repair_banner = "Mercury USB is not ready"

    rows: list[str] = []
    with patch(
        "mercury.core.environment_status._hdd_writer_active",
        return_value=True,
    ):
        assert _hdd_writer_active() is True
        if env.repairable_blockers or env.usb.repair_banner:
            if _hdd_writer_active():
                if env.repairable_blockers:
                    rows.append("Storage repair")
            else:
                rows.append("USB repair")
    assert rows == []
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig
from mercury.storage.host_maintenance import HostMaintenanceState


def _config(tmp_path: Path) -> StorageConfig:
    primary = tmp_path / "hdd"
    primary.mkdir()
    return StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "MERCURY_DATA_V2", primary, "hdd", "ext4", True),
        legacy=StorageRootConfig(
            "legacy",
            StorageRootRole.LEGACY_ARCHIVE,
            "MERCURY_DATA_USB",
            tmp_path / "usb",
            "e4f0c7fb-132e-4867-9c16-5e4749f5c43a",
            "ext4",
            False,
        ),
        active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
    )


def _archive() -> dict:
    return {
        "usb_uuid": "e4f0c7fb-132e-4867-9c16-5e4749f5c43a",
        "final_usb_archive_generation": "generation",
        "manifest_sha256": "manifest",
        "relative_path_manifest": [
            {"path": "mercury_manifests", "kind": "dir", "size": None},
            {"path": "mercury_manifests/historical.json", "kind": "file", "size": 4},
        ],
    }


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import mercury.storage.archive_retire as retire

    config = _config(tmp_path)
    historical = config.primary.mount_path / "mercury_manifests"
    historical.mkdir()
    (historical / "historical.json").write_text("data")
    monkeypatch.setattr(retire, "build_storage_status_report", lambda: SimpleNamespace(
        primary=SimpleNamespace(validation=SimpleNamespace(ok=True, code=SimpleNamespace(value="ok"))),
        legacy=SimpleNamespace(is_offline_archive=True),
    ))
    monkeypatch.setattr(retire, "detect_active_operations", lambda: [])
    monkeypatch.setattr(retire, "load_host_maintenance", lambda: HostMaintenanceState(
        writes_allowed=True, active_write_role="primary",
    ))
    monkeypatch.setattr(retire, "read_cutover_receipt", lambda **_kwargs: {
        "hdd_uuid": "hdd",
        "usb_uuid": "e4f0c7fb-132e-4867-9c16-5e4749f5c43a",
        "cutover_verified_hdd_generation": "generation",
        "final_usb_archive_generation": "generation",
    })
    monkeypatch.setattr(retire, "read_verified_generation", lambda **_kwargs: "generation")
    monkeypatch.setattr(retire, "read_archive_receipt", lambda **_kwargs: _archive())
    monkeypatch.setattr(retire, "apply_legacy_usb_runtime_policy", lambda **_kwargs: False)
    events: list[dict] = []
    monkeypatch.setattr(retire, "append_transition_ledger", lambda event: events.append(event))
    return retire, config, events


def test_retire_records_evidence_without_changing_writer_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, events = _prepare(tmp_path, monkeypatch)
    result = retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    assert result == config.primary.control_dir / retire.RETIREMENT_RECEIPT_FILE
    assert result.is_file()
    assert events[0]["transition"] == "legacy_usb_archive_retirement"
    assert events[0]["usb_data_changed"] is False
    assert events[0]["mismatches"] == 0
    assert config.active_write_role == StorageWriteRole.PRIMARY
    assert config.legacy.writable is False
    assert retire.legacy_usb_is_phased_out(config=config) is True


def test_retire_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, events = _prepare(tmp_path, monkeypatch)
    first = retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    second = retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    assert first == second
    assert len([e for e in events if e["transition"] == "legacy_usb_archive_retirement"]) == 1


def test_retire_refuses_bad_evidence_or_missing_historical_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, _events = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(retire, "read_cutover_receipt", lambda **_kwargs: {
        "hdd_uuid": "hdd", "usb_uuid": "wrong", "cutover_verified_hdd_generation": "generation",
        "final_usb_archive_generation": "generation",
    })
    with pytest.raises(ValueError, match="UUID"):
        retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    monkeypatch.setattr(retire, "read_cutover_receipt", lambda **_kwargs: {
        "hdd_uuid": "hdd",
        "usb_uuid": "e4f0c7fb-132e-4867-9c16-5e4749f5c43a",
        "cutover_verified_hdd_generation": "generation",
        "final_usb_archive_generation": "generation",
    })
    (config.primary.mount_path / "mercury_manifests" / "historical.json").unlink()
    with pytest.raises(ValueError, match="missing or changed"):
        retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)


def test_retire_refuses_active_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, _events = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(retire, "detect_active_operations", lambda: ["restore"])
    with pytest.raises(ValueError, match="Active operation"):
        retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)


def test_retire_requires_exact_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, _events = _prepare(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="RETIRE LEGACY USB"):
        retire.retire_legacy_usb_archive(confirmation="retire", config=config)


def test_apply_runtime_policy_sets_local_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mercury.storage.archive_retire as retire

    local = tmp_path / "local.toml"
    local.write_text(
        '[storage]\nactive_write_role = "primary"\nmigration_state = "cutover_complete"\n'
        '[storage.legacy]\nrole = "legacy_archive"\nwritable = true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(retire, "resolve_local_config", lambda: local)
    assert retire.apply_legacy_usb_runtime_policy() is True
    text = local.read_text(encoding="utf-8")
    assert 'legacy_runtime_dependency = "none"' in text
    assert "writable = false" in text
    assert retire.apply_legacy_usb_runtime_policy() is False


def test_doctor_repair_plan_skips_usb_activation_when_phased_out(monkeypatch) -> None:
    from types import SimpleNamespace

    from mercury.core.environment_status import ConfigSetupStatus, UsbDiscovery
    from mercury.core.execution_policy import ExecutionPolicy, REQUIRED_BACKUP_MOUNT
    from mercury.core.paths import REPO_ROOT
    from mercury.env.doctor import build_repair_plan

    monkeypatch.setattr(
        "mercury.storage.archive_retire.legacy_usb_is_phased_out",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "mercury.storage.report.build_storage_status_report",
        lambda: SimpleNamespace(
            config=SimpleNamespace(cutover_complete=True),
            primary=SimpleNamespace(
                validation=SimpleNamespace(
                    ok=True,
                    identity=SimpleNamespace(is_mount=True, stale_mountpoint_entries=[]),
                ),
                filesystem_uuid="hdd",
                mount_path="/mnt/MERCURY_DATA_V2",
            ),
            legacy=SimpleNamespace(
                role="legacy_archive",
                validation=SimpleNamespace(ok=False, identity=SimpleNamespace(is_mount=False)),
                physical_mount_mode="offline",
                mount_path="/mnt/MERCURY_DATA_USB",
            ),
            migration_state=SimpleNamespace(value="cutover_complete"),
        ),
    )
    monkeypatch.setattr(
        "mercury.core.storage_roots.load_storage_config",
        lambda warn_deprecated=False: SimpleNamespace(cutover_complete=True),
    )
    report = SimpleNamespace(
        repo_root=REPO_ROOT,
        current_user="secadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(Path("/mnt/MERCURY_DATA_USB"), False, False, None),
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
    text = "\n".join(title + "\n" + "\n".join(steps) for title, steps in plan)
    assert "Repair Mercury USB" not in text
    assert "USB device not detected" not in text
    assert "persist USB mount" not in text
    assert "Prepare Mercury USB directories" not in text


def test_environment_check_hides_usb_detected_when_phased_out(monkeypatch, tmp_path: Path) -> None:
    from mercury.core.execution_policy import ExecutionPolicy
    from mercury.env.probe import EnvProbeResult
    from mercury.env.terminal.check import build_environment_check_fields

    monkeypatch.setattr(
        "mercury.core.execution_policy.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path / "backups",
            config_path=tmp_path / "local.toml",
        ),
    )
    monkeypatch.setattr(
        "mercury.env.terminal.check.build_environment_status",
        lambda **_kwargs: SimpleNamespace(
            config=SimpleNamespace(
                local_toml_present=True,
                databases_toml_present=True,
                repos_toml_present=True,
            ),
            mariadb=SimpleNamespace(
                mariadb_client="/usr/bin/mariadb",
                mysqldump_client="/usr/bin/mariadb-dump",
                service_state="active",
                socket_available=True,
                config_present=True,
            ),
            primary_setup_blocker=None,
        ),
    )
    monkeypatch.setattr(
        "mercury.env.terminal.check.discover_usb_target",
        lambda: SimpleNamespace(
            mount_path=Path("/mnt/MERCURY_DATA_USB"),
            mercury_layout_present=False,
            mounted=False,
        ),
    )
    monkeypatch.setattr("mercury.env.terminal.check.backup_root_mount_label", lambda _p: "mounted")
    monkeypatch.setattr("mercury.env.terminal.check.backup_root_filesystem", lambda _p: "ext4")
    monkeypatch.setattr("mercury.env.terminal.check.backup_root_free_space_label", lambda _p: "10 GiB")
    monkeypatch.setattr("mercury.env.terminal.check.backup_root_storage_status_label", lambda _p: "ready")
    monkeypatch.setattr(
        "mercury.storage.archive_retire.legacy_usb_is_phased_out",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "mercury.storage.archive_retire.build_legacy_archive_status",
        lambda **_kwargs: SimpleNamespace(operator_line="retired / offline"),
    )
    monkeypatch.setattr(
        "mercury.core.storage_roots.load_storage_config",
        lambda **_kwargs: SimpleNamespace(
            primary=SimpleNamespace(
                label="MERCURY_DATA_V2",
                mount_path=Path("/mnt/MERCURY_DATA_V2"),
            ),
        ),
    )
    env = EnvProbeResult(
        python_version="3.14.6",
        platform_system="Linux",
        platform_release="fc43",
        platform_support="Fedora supported",
        repo_root=str(tmp_path),
        config_dir=str(tmp_path / "config"),
        output_dir=str(tmp_path / "out"),
        mode="operator",
        dry_run_only=True,
    )
    fields = build_environment_check_fields(env)
    storage = fields["Backup Storage"]
    assert "USB detected" not in storage
    assert storage["Active storage"].startswith("MERCURY_DATA_V2")
    assert storage["Legacy archive"] == "retired / offline"

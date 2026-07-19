from __future__ import annotations

from mercury.core.storage_roots import default_storage_config
from mercury.core.usb_mount import assert_operator_storage_path, resolve_operator_mount
from mercury.core.execution_policy import load_execution_policy
from mercury.storage.cutover_plan import build_cutover_plan


def test_cutover_plan_lists_all_coordinated_writer_paths(monkeypatch) -> None:
    cfg = default_storage_config()
    monkeypatch.setattr(
        "mercury.storage.cutover_plan.build_cutover_readiness",
        lambda **_kwargs: type("Readiness", (), {"ready": True, "active_write_role": "legacy"})(),
    )

    plan = build_cutover_plan(config=cfg)

    assert [change.key for change in plan.path_changes] == [
        "backup_root", "log_dir", "repo_backup_root", "manifest_dir", "runbook_dir",
    ]
    assert all("MERCURY_DATA_USB" in change.legacy_path for change in plan.path_changes)
    assert all("MERCURY_DATA_V2" in change.primary_path for change in plan.path_changes)
    assert plan.execution_available is False
    assert plan.ready_for_future_execution is False
    assert plan.runtime_blockers


def test_operator_mount_uses_primary_when_primary_role_is_configured(tmp_path) -> None:
    primary = tmp_path / "primary"
    config = tmp_path / "local.toml"
    config.write_text(
        "\n".join(
            [
                "[storage]",
                'active_write_role = "primary"',
                "[storage.primary]",
                f'mount_path = "{primary}"',
            ]
        ),
        encoding="utf-8",
    )

    assert resolve_operator_mount(local_config=config) == primary.resolve()


def test_operator_storage_guard_accepts_primary_writer_path(tmp_path, monkeypatch) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    config = tmp_path / "local.toml"
    config.write_text(
        "\n".join(
            [
                "[storage]",
                'active_write_role = "primary"',
                "[storage.primary]",
                f'mount_path = "{primary}"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mercury.core.usb_mount.usb_mount_is_active", lambda _mount: True
    )

    assert_operator_storage_path(
        primary / "mercury_repo_backups",
        operator_mount=resolve_operator_mount(local_config=config),
    )


def test_execution_policy_uses_primary_mount_after_coordinated_path_change(tmp_path) -> None:
    primary = tmp_path / "primary"
    backup_root = primary / "mercury_backups"
    backup_root.mkdir(parents=True)
    config = tmp_path / "local.toml"
    config.write_text(
        "\n".join(
            [
                "[mercury]",
                f'backup_root = "{backup_root}"',
                "[storage]",
                'active_write_role = "primary"',
                "[storage.primary]",
                f'mount_path = "{primary}"',
            ]
        ),
        encoding="utf-8",
    )

    policy = load_execution_policy(local_config=config)

    assert policy.usb_mount == primary.resolve()
    assert policy.backup_root == backup_root.resolve()
    assert policy.backup_root_is_under_required_mount()

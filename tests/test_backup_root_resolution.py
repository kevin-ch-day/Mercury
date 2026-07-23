"""Hermetic resolve_backup_root precedence and live-path isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.execution_policy import resolve_backup_root
from mercury.core.storage_roles import ENV_BACKUP_ROOT
from mercury.storage.host_maintenance import assert_not_live_mercury_path


def test_explicit_absolute_temporary_backup_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller-supplied config backup_root wins over hermetic MERCURY_BACKUP_ROOT."""
    root = tmp_path / "explicit_backups"
    root.mkdir()
    config = tmp_path / "local.toml"
    config.write_text(f'[mercury]\nbackup_root = "{root}"\n', encoding="utf-8")
    # Hermetic env remains set by autouse fixture — must not replace explicit config.
    resolved = resolve_backup_root(local_config=config)
    assert resolved == root.resolve()


def test_explicit_legacy_usb_shaped_fixture_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usb_root = tmp_path / "run" / "media" / "secadmin" / "MERCURY_USB" / "mercury_backups"
    usb_root.mkdir(parents=True)
    config = tmp_path / "local.toml"
    config.write_text(f'[mercury]\nbackup_root = "{usb_root}"\n', encoding="utf-8")
    resolved = resolve_backup_root(local_config=config)
    assert resolved == usb_root.resolve()
    assert "MERCURY_USB" in str(resolved)


def test_environment_provided_backup_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_root = tmp_path / "env_backups"
    env_root.mkdir()
    monkeypatch.setenv(ENV_BACKUP_ROOT, str(env_root))
    # No explicit local_config → ambient env wins.
    resolved = resolve_backup_root()
    assert resolved == env_root.resolve()


def test_configuration_provided_root_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_root = tmp_path / "cfg_backups"
    cfg_root.mkdir()
    config = tmp_path / "local.toml"
    config.write_text(f'[mercury]\nbackup_root = "{cfg_root}"\n', encoding="utf-8")
    monkeypatch.delenv(ENV_BACKUP_ROOT, raising=False)
    resolved = resolve_backup_root(local_config=config)
    assert resolved == cfg_root.resolve()


def test_caller_provided_env_root_parameter_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_root = tmp_path / "ambient"
    env_root.mkdir()
    caller_root = tmp_path / "caller"
    caller_root.mkdir()
    monkeypatch.setenv(ENV_BACKUP_ROOT, str(env_root))
    config = tmp_path / "local.toml"
    config.write_text(
        f'[mercury]\nbackup_root = "{tmp_path / "cfg"}"\n', encoding="utf-8"
    )
    resolved = resolve_backup_root(local_config=config, env_root=str(caller_root))
    assert resolved == caller_root.resolve()


def test_real_mercury_data_v2_writes_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="TEST ISOLATION"):
        assert_not_live_mercury_path(
            Path("/mnt/MERCURY_DATA_V2/.mercury_control/backup_sync_sessions/x"),
            purpose="test write",
        )


def test_real_mercury_data_usb_writes_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="TEST ISOLATION"):
        assert_not_live_mercury_path(
            Path("/mnt/MERCURY_DATA_USB/mercury_backups/x"),
            purpose="test write",
        )


def test_resolve_backup_root_accepts_absolute_usb_path(tmp_path: Path) -> None:
    """Regression: explicit local_config must not be shadowed by hermetic env."""
    usb_root = tmp_path / "run" / "media" / "secadmin" / "MERCURY_USB" / "mercury_backups"
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        "[mercury]\n"
        f'backup_root = "{usb_root}"\n',
        encoding="utf-8",
    )
    resolved = resolve_backup_root(local_config=config_path)
    assert resolved == usb_root.resolve()

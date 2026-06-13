"""Tests for local.toml USB path repair."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.config.init import missing_mercury_usb_artifact_keys, repair_local_config_paths


def test_missing_mercury_usb_artifact_keys_detects_gaps(tmp_path: Path) -> None:
    local = tmp_path / "local.toml"
    local.write_text(
        "[mercury]\nbackup_root = \"/mnt/MERCURY_DATA_USB/mercury_backups\"\n",
        encoding="utf-8",
    )
    missing = missing_mercury_usb_artifact_keys(local_config=local)
    assert missing == ["repo_backup_root", "manifest_dir", "runbook_dir"]


def test_repair_local_config_paths_adds_missing_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    local = config_dir / "local.toml"
    local.write_text(
        "[mercury]\n"
        'backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"\n'
        'log_dir = "/mnt/MERCURY_DATA_USB/mercury_logs"\n'
        "\n"
        "[mariadb]\n"
        'user = "secadmin"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("mercury.config.init.LOCAL_CONFIG", local)
    monkeypatch.setattr(
        "mercury.config.init.discover_usb_target",
        lambda: type(
            "Usb",
            (),
            {"mercury_layout_present": False, "mount_path": Path("/mnt/MERCURY_DATA_USB")},
        )(),
    )

    notes = repair_local_config_paths()
    text = local.read_text(encoding="utf-8")
    assert "repo_backup_root" in text
    assert "manifest_dir" in text
    assert "runbook_dir" in text
    assert 'user = "secadmin"' in text
    assert any("added repo_backup_root" in note for note in notes)

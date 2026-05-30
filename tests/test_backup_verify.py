"""Tests for backup verify and locate helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury.backup.dump_planner import build_dump_argv_for_config
from mercury.backup.execute import execute_backup
from mercury.backup.locate import find_latest_backup_directory
from mercury.backup.verification import verify_backup_directory
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.config import MariaDbConnectionConfig

FIXED_DATE = "2026-05-30"
FIXED_TS = "20260530_120000"
FIXED_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_build_dump_argv_supports_unix_socket() -> None:
    cfg = MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    argv = build_dump_argv_for_config("erebus_threat_intel_prod", BACKUP_KIND_FULL, cfg)
    assert "--socket=/var/lib/mysql/mysql.sock" in argv
    assert "--protocol=SOCKET" in argv
    assert "-h" not in argv


def test_find_latest_backup_directory(tmp_path: Path) -> None:
    older = tmp_path / "2026-05-29" / "erebus_threat_intel_prod"
    newer = tmp_path / "2026-05-30" / "erebus_threat_intel_prod"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "manifest.json").write_text("{}", encoding="utf-8")
    (newer / "manifest.json").write_text("{}", encoding="utf-8")

    latest = find_latest_backup_directory(tmp_path, "erebus_threat_intel_prod")
    assert latest == newer


def test_verify_backup_directory_updates_manifest(tmp_path: Path) -> None:
    policy = ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=tmp_path)

    def fake_runner(argv, env, output_path, _config):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"backup-bytes\n")

    execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        execute=True,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
        mariadb_config=MariaDbConnectionConfig(
            host="127.0.0.1", port=3306, user="root", password=""
        ),
        dump_runner=fake_runner,
    )

    backup_dir = tmp_path / FIXED_DATE / "erebus_threat_intel_prod"
    result = verify_backup_directory(backup_dir, update_manifest=True)
    assert result.verified is True

    import json

    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["verified"] is True


def test_cli_backup_verify_missing_backup() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mercury.cli",
            "backup",
            "verify",
            "--db",
            "erebus_threat_intel_prod",
            "--no-latest",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "provide --path" in (result.stdout + result.stderr).lower()

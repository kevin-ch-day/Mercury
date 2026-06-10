"""Tests for sync readiness freshness gating."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.backup.checksum import write_checksum_file
from mercury.backup.freshness import FRESHNESS_STALE
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.core import DatabaseInventory, record_from_name
from mercury.database.core.sources import SOURCE_LIVE
from mercury.sync.readiness import build_sync_readiness_report


def _write_verified_backup(tmp_path: Path, database: str) -> None:
    backup_dir = tmp_path / "2026-06-09" / database
    backup_dir.mkdir(parents=True)
    dump_name = f"{database}_20260609_030000_000.sql.gz"
    schema_name = f"{database}_20260609_030000_000.schema.sql.gz"
    (backup_dir / dump_name).write_bytes(b"backup-data\n")
    (backup_dir / schema_name).write_bytes(b"schema-bytes\n")
    write_checksum_file(backup_dir, [dump_name, schema_name])
    manifest = {
        "backup_id": f"{database}-full-20260609_030000_000",
        "database": database,
        "backup_kind": BACKUP_KIND_FULL,
        "created_at": "2026-06-09T03:00:00+00:00",
        "dump_file": dump_name,
        "schema_file": schema_name,
        "size_bytes": 12,
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_sync_readiness_blocks_stale_verified_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_verified_backup(tmp_path, "erebus_threat_intel_prod")
    inventory = DatabaseInventory(
        connection="connected",
        entries=[
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE, connected=True),
            record_from_name("erebus_threat_intel_dev", SOURCE_LIVE, connected=True),
        ],
    )
    monkeypatch.setattr(
        "mercury.sync.readiness.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path,
            allow_unsafe_backup_root=True,
        ),
    )
    monkeypatch.setattr("mercury.sync.readiness.discover", lambda _mode: inventory)
    monkeypatch.setattr("mercury.sync.readiness.should_probe_database_status", lambda: True)
    monkeypatch.setattr(
        "mercury.sync.readiness.assess_backup_freshness",
        lambda database, backup_at, live=True, **kwargs: type(
            "Freshness",
            (),
            {"freshness": FRESHNESS_STALE},
        )(),
    )

    report = build_sync_readiness_report(live=True)
    entry = next(item for item in report.entries if item.prod == "erebus_threat_intel_prod")
    assert entry.backup_verified is True
    assert entry.backup_freshness == FRESHNESS_STALE
    assert entry.ready_for_sync_planning is False
    assert any("freshness is stale" in blocker for blocker in entry.blockers)
    assert report.ready_count == 0


def test_sync_readiness_uses_artifact_verified_blocker_wording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_dir = tmp_path / "2026-06-09" / "erebus_threat_intel_prod"
    backup_dir.mkdir(parents=True)
    (backup_dir / "manifest.json").write_text("{}", encoding="utf-8")
    inventory = DatabaseInventory(
        connection="connected",
        entries=[
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE, connected=True),
            record_from_name("erebus_threat_intel_dev", SOURCE_LIVE, connected=True),
        ],
    )
    monkeypatch.setattr(
        "mercury.sync.readiness.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path,
            allow_unsafe_backup_root=True,
        ),
    )
    monkeypatch.setattr("mercury.sync.readiness.discover", lambda _mode: inventory)
    monkeypatch.setattr("mercury.sync.readiness.should_probe_database_status", lambda: False)

    report = build_sync_readiness_report(live=True)
    entry = next(item for item in report.entries if item.prod == "erebus_threat_intel_prod")
    assert any("artifact-verified" in blocker for blocker in entry.blockers)

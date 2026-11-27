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
    monkeypatch.setattr(
        "mercury.sync.readiness.discover_for_planning",
        lambda live=True: inventory,
    )
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
    monkeypatch.setattr(
        "mercury.sync.readiness.discover_for_planning",
        lambda live=True: inventory,
    )
    monkeypatch.setattr("mercury.sync.readiness.should_probe_database_status", lambda: False)

    report = build_sync_readiness_report(live=True)
    entry = next(item for item in report.entries if item.prod == "erebus_threat_intel_prod")
    assert any("artifact-verified" in blocker for blocker in entry.blockers)


def test_sync_readiness_blocks_undumpable_production_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.backup.dump_preflight import DatabaseDumpability, SourcesDumpability
    from mercury.backup.freshness import FRESHNESS_FRESH

    _write_verified_backup(tmp_path, "scytaledroid_core_prod")
    inventory = DatabaseInventory(
        connection="connected",
        entries=[
            record_from_name("scytaledroid_core_prod", SOURCE_LIVE, connected=True),
            record_from_name("scytaledroid_core_dev", SOURCE_LIVE, connected=True),
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
    monkeypatch.setattr(
        "mercury.sync.readiness.discover_for_planning",
        lambda live=True: inventory,
    )
    monkeypatch.setattr("mercury.sync.readiness.should_probe_database_status", lambda: True)
    monkeypatch.setattr(
        "mercury.sync.readiness.assess_backup_freshness",
        lambda database, backup_at, live=True, **kwargs: type(
            "Freshness",
            (),
            {"freshness": FRESHNESS_FRESH},
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.dump_preflight.try_assess_sources_dumpability",
        lambda sources, **kwargs: SourcesDumpability(
            entries=[
                DatabaseDumpability(
                    database="scytaledroid_core_prod",
                    view_count=51,
                    issues=["view `v_masvs_matrix` is not dumpable (collation mix a vs b)"],
                )
            ]
        ),
    )

    report = build_sync_readiness_report(live=True)
    entry = next(item for item in report.entries if item.prod == "scytaledroid_core_prod")
    assert entry.ready_for_sync_planning is False
    assert any("not dumpable" in blocker for blocker in entry.blockers)
    assert any("ScytaleDroid" in blocker for blocker in entry.blockers)
    assert report.ready_count == 0


def test_live_source_object_contract_detects_names_added_after_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.backup.content_contract import (
        BackupContentContract,
        BackupObjectInventory,
    )
    from mercury.database.mariadb.config import MariaDbConnectionConfig
    from mercury.sync.readiness import _live_source_object_issue

    contract = BackupContentContract(
        live=BackupObjectInventory(tables=["original"]),
        dump=BackupObjectInventory(tables=["original"]),
        verified=True,
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"object_contract": contract.model_dump()}), encoding="utf-8"
    )
    config = MariaDbConnectionConfig(host="localhost", user="reader")
    monkeypatch.setattr(
        "mercury.database.mariadb.session.try_load_mariadb_config", lambda: config
    )
    monkeypatch.setattr(
        "mercury.sync.readiness.fetch_live_object_inventory",
        lambda *_a: BackupObjectInventory(tables=["original", "new_table"]),
    )
    issue = _live_source_object_issue(tmp_path, "android_permission_intel")
    assert issue and "differ" in issue and "refresh" in issue


def test_sync_verification_does_not_call_historical_target_match_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.restore.readiness import TargetCompletenessEntry
    from mercury.sync.readiness import SyncReadinessEntry, SyncReadinessReport
    from mercury.sync.verification import build_sync_verification_report

    readiness = SyncReadinessReport(
        mode="live", backup_root="/tmp/backups",
        entries=[SyncReadinessEntry(
            prod="android_permission_intel",
            expected_dev="android_permission_intel_dev",
            dev_listed=True,
            blockers=["Live source object names differ from verified backup"],
        )],
    )
    monkeypatch.setattr(
        "mercury.sync.verification.build_sync_readiness_report", lambda live: readiness
    )
    monkeypatch.setattr(
        "mercury.sync.verification.should_probe_database_status", lambda: True
    )
    monkeypatch.setattr(
        "mercury.sync.verification.build_target_completeness_entry_against_backup",
        lambda **_kwargs: TargetCompletenessEntry(
            database="android_permission_intel",
            target_database="android_permission_intel_dev",
            completeness_status="complete",
        ),
    )
    report = build_sync_verification_report(live=True)
    assert report.complete_count == 0
    assert report.entries[0].status == "source_unready"
    assert report.entries[0].ready is False


def test_unready_permission_intel_blocks_dependent_sync_pairs() -> None:
    from mercury.sync.readiness import SyncReadinessEntry, _block_unready_dependencies

    entries = [
        SyncReadinessEntry(
            prod="android_permission_intel", expected_dev="android_permission_intel_dev",
            dev_listed=True, sync_order=1, ready_for_sync_planning=False,
        ),
        SyncReadinessEntry(
            prod="erebus_threat_intel_prod", expected_dev="erebus_threat_intel_dev",
            dev_listed=True, sync_order=2, ready_for_sync_planning=True,
            depends_on_sources=["android_permission_intel"],
        ),
        SyncReadinessEntry(
            prod="scytaledroid_core_prod", expected_dev="scytaledroid_core_dev",
            dev_listed=True, sync_order=3, ready_for_sync_planning=True,
            depends_on_sources=["android_permission_intel"],
        ),
    ]
    _block_unready_dependencies(entries)
    assert [entry.ready_for_sync_planning for entry in entries] == [False, False, False]
    assert all("upstream" in entry.blockers[-1] for entry in entries[1:])

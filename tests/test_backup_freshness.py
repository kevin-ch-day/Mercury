"""Tests for backup freshness semantics (verified vs current)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury.backup.freshness import (
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    artifact_status_label,
    assess_backup_freshness,
    parse_backup_timestamp,
)
from mercury.backup.interactive_menu import _artifact_and_freshness, _backup_screen_rows
from mercury.backup.status import build_backup_status_report
from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.backup_planning import build_backup_plan


def test_verified_artifact_is_not_labeled_current() -> None:
    artifact, freshness = _artifact_and_freshness(
        type(
            "Entry",
            (),
            {"protection_status": "verified", "freshness": FRESHNESS_UNKNOWN},
        )()
    )
    assert artifact == "verified"
    assert artifact != "current"
    assert freshness == "unknown"


def test_assess_backup_freshness_marks_stale_when_activity_after_backup() -> None:
    backup_at = parse_backup_timestamp("2026-06-09T03:01:29+00:00")
    activity_at = parse_backup_timestamp("2026-06-09T22:00:57+00:00")
    assert backup_at is not None
    assert activity_at is not None

    def fake_scalar(_config, _sql) -> str:
        return activity_at.strftime("%Y-%m-%d %H:%M:%S")

    assessment = assess_backup_freshness(
        "erebus_threat_intel_prod",
        backup_at=backup_at,
        live=True,
        config=object(),  # type: ignore[arg-type]
        scalar_fn=fake_scalar,
    )
    assert assessment.freshness == FRESHNESS_STALE
    assert assessment.recommend_full_backup is True


def test_assess_backup_freshness_marks_fresh_when_activity_before_backup() -> None:
    backup_at = parse_backup_timestamp("2026-06-09T22:00:57+00:00")
    activity_at = parse_backup_timestamp("2026-06-09T03:01:29+00:00")

    def fake_scalar(_config, _sql) -> str:
        return activity_at.strftime("%Y-%m-%d %H:%M:%S")

    assessment = assess_backup_freshness(
        "erebus_threat_intel_prod",
        backup_at=backup_at,
        live=True,
        config=object(),  # type: ignore[arg-type]
        scalar_fn=fake_scalar,
    )
    assert assessment.freshness == FRESHNESS_FRESH
    assert assessment.recommend_full_backup is False


def test_assess_backup_freshness_unknown_without_activity_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mercury.backup.freshness.should_probe_database_status", lambda: True)

    def fake_scalar(_config, _sql) -> str:
        return ""

    assessment = assess_backup_freshness(
        "erebus_threat_intel_prod",
        backup_at=parse_backup_timestamp("2026-06-09T03:01:29+00:00"),
        live=True,
        config=object(),  # type: ignore[arg-type]
        scalar_fn=fake_scalar,
    )
    assert assessment.freshness == FRESHNESS_UNKNOWN
    assert assessment.recommend_full_backup is True


def test_backup_screen_rows_never_use_current_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mercury.backup.interactive_menu.build_prod_dev_pairs", lambda names: [])
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.latest_records_by_database",
        lambda listing: [
            type(
                "Record",
                (),
                {
                    "database": "android_permission_intel",
                    "created_at": "2026-06-09T03:01:26+00:00",
                },
            )()
        ],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_on_disk_backup_list",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_backup_status_report",
        lambda live=False: type(
            "Report",
            (),
            {
                "entries": [
                    type(
                        "Entry",
                        (),
                        {
                            "database": "android_permission_intel",
                            "protection_status": "verified",
                            "freshness": FRESHNESS_STALE,
                            "backup_age": "19h ago",
                        },
                    )()
                ],
                "stale_count": 1,
                "unknown_freshness_count": 0,
            },
        )(),
    )

    plan = build_backup_plan(["android_permission_intel"])
    rows = _backup_screen_rows(plan)
    flattened = " ".join(value for row in rows for value in row)
    assert "current" not in flattened
    assert "verified" in flattened
    assert "stale" in flattened


def test_build_backup_status_report_includes_freshness_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from mercury.backup.checksum import write_checksum_file
    from mercury.core.safety import BACKUP_KIND_FULL

    backup_dir = tmp_path / "2026-06-09" / "erebus_threat_intel_prod"
    backup_dir.mkdir(parents=True)
    dump_name = "erebus_threat_intel_prod_20260609_030129_729.sql.gz"
    schema_name = "erebus_threat_intel_prod_20260609_030129_729.schema.sql.gz"
    (backup_dir / dump_name).write_bytes(b"backup\n")
    with __import__("gzip").open(backup_dir / schema_name, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `alpha` (\n  id INT\n);\n")
    write_checksum_file(backup_dir, [dump_name, schema_name])
    manifest = {
        "backup_id": "erebus_threat_intel_prod-full-20260609_030129_729",
        "database": "erebus_threat_intel_prod",
        "backup_kind": BACKUP_KIND_FULL,
        "created_at": "2026-06-09T03:01:29+00:00",
        "dump_file": dump_name,
        "schema_file": schema_name,
        "size_bytes": 7,
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path,
        allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr(
        "mercury.backup.status.assess_backup_freshness",
        lambda database, backup_at, live=True: type(
            "Freshness",
            (),
            {
                "freshness": FRESHNESS_STALE,
                "latest_source_activity_at": datetime(2026, 6, 9, 22, 0, 57, tzinfo=timezone.utc),
                "activity_signal": "virustotal_run_ledger.finished_at_utc",
                "backup_age": "19h ago",
                "recommend_full_backup": True,
                "notes": [],
            },
        )(),
    )

    report = build_backup_status_report(
        live=True,
        selected=["erebus_threat_intel_prod"],
        policy=policy,
    )
    entry = report.entries[0]
    assert entry.protection_status == "verified"
    assert entry.freshness == FRESHNESS_STALE
    assert entry.recommend_full_backup is True
    assert report.stale_count == 1


def test_artifact_status_label_maps_verified_without_current_wording() -> None:
    assert artifact_status_label("verified") == "verified"
    assert artifact_status_label("verified") != "current"


def test_restore_readiness_complete_while_freshness_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from mercury.backup.checksum import write_checksum_file
    from mercury.core.safety import BACKUP_KIND_FULL
    from mercury.database.mariadb.inspect import DatabaseInspectResult
    from mercury.restore.readiness import EREBUS_CANONICAL_TABLES, build_target_completeness_entry

    table_names = list(EREBUS_CANONICAL_TABLES) + [
        f"table_{index:03d}" for index in range(125 - len(EREBUS_CANONICAL_TABLES))
    ]
    backup_dir = tmp_path / "2026-06-09" / "erebus_threat_intel_prod"
    backup_dir.mkdir(parents=True)
    dump_name = "erebus_threat_intel_prod_20260609_030129_729.sql.gz"
    schema_name = "erebus_threat_intel_prod_20260609_030129_729.schema.sql.gz"
    (backup_dir / dump_name).write_bytes(b"backup\n")
    lines = [f"CREATE TABLE `{name}` (\n  id INT\n);\n" for name in table_names]
    lines.extend(f"CREATE VIEW `view_{index}` AS SELECT 1;\n" for index in range(76))
    with __import__("gzip").open(backup_dir / schema_name, "wt", encoding="utf-8") as handle:
        handle.writelines(lines)
    write_checksum_file(backup_dir, [dump_name, schema_name])
    manifest = {
        "backup_id": "erebus_threat_intel_prod-full-20260609_030129_729",
        "database": "erebus_threat_intel_prod",
        "backup_kind": BACKUP_KIND_FULL,
        "created_at": "2026-06-09T03:01:29+00:00",
        "dump_file": dump_name,
        "schema_file": schema_name,
        "size_bytes": 7,
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        "mercury.restore.readiness.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path,
            allow_unsafe_backup_root=True,
        ),
    )
    monkeypatch.setattr("mercury.restore.readiness.should_probe_database_status", lambda: True)

    def fake_inspect(name: str, _config) -> DatabaseInspectResult:
        return DatabaseInspectResult(
            name=name,
            role="production",
            backup_source=True,
            exists_on_server=True,
            connected=True,
            table_count=125,
            view_count=76,
            total_bytes=1024,
        )

    monkeypatch.setattr(
        "mercury.restore.readiness.fetch_live_base_table_names",
        lambda *_args, **_kwargs: table_names,
    )

    completeness = build_target_completeness_entry(
        "erebus_threat_intel_prod",
        live=True,
        config=object(),  # type: ignore[arg-type]
        inspect_fn=fake_inspect,
    )
    freshness = assess_backup_freshness(
        "erebus_threat_intel_prod",
        backup_at=parse_backup_timestamp("2026-06-09T03:01:29+00:00"),
        live=True,
        config=object(),  # type: ignore[arg-type]
        scalar_fn=lambda _config, _sql: "2026-06-09 22:00:57",
    )

    assert completeness.completeness_status == "complete"
    assert completeness.live_object_count == 201
    assert freshness.freshness == FRESHNESS_STALE

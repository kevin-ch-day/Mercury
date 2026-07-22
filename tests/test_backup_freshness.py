"""Tests for backup freshness semantics (verified vs current)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury.backup.freshness import (
    FRESHNESS_EMPTY,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    artifact_status_label,
    assess_backup_freshness,
    parse_backup_timestamp,
)
from mercury.backup.interactive_menu import _backup_screen_rows, _status_label
from mercury.backup.status import build_backup_status_report
from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.backup_planning import build_backup_plan


def test_verified_unknown_status_is_not_labeled_fresh() -> None:
    status = _status_label(
        type(
            "Entry",
            (),
            {"protection_status": "verified", "freshness": FRESHNESS_UNKNOWN},
        )()
    )
    assert status == "Unknown"
    assert status != "Fresh"


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


def test_assess_backup_freshness_marks_verified_empty_source_without_activity_probe() -> None:
    assessment = assess_backup_freshness(
        "obsidiandroid_core_prod",
        backup_at=parse_backup_timestamp("2026-07-20T00:00:00+00:00"),
        live=True,
        source_is_empty=True,
    )
    assert assessment.freshness == FRESHNESS_EMPTY
    assert assessment.recommend_full_backup is False


def test_obsidiandroid_freshness_probes_match_the_current_core_schema() -> None:
    from mercury.backup.freshness import SOURCE_ACTIVITY_PROBES

    probes = SOURCE_ACTIVITY_PROBES["obsidiandroid_core_prod"]
    labels = {label for label, _statement in probes}
    sql = "\n".join(statement for _label, statement in probes)
    assert "core_schema_migration.applied_at_utc" in labels
    assert "core_artifact.imported_at_utc" in labels
    assert "obsidiandroid_core_prod.core_schema_migration" in sql
    assert "schema_migrations " not in sql


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
                    "size_bytes": 10465313,
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
    assert "Stale" in flattened


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
        lambda database, backup_at, live=True, **_kwargs: type(
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


def test_display_status_labels() -> None:
    from mercury.backup.freshness import (
        backup_entry_status_label,
        display_artifact_status_label,
        display_freshness_label,
        menu_handoff_problem_summary,
        protection_handoff_action_item,
    )

    assert display_artifact_status_label("failed") == "Unverified"
    assert display_freshness_label("stale") == "Stale"
    assert display_freshness_label("empty") == "Empty"
    assert display_freshness_label(None) == "—"
    assert backup_entry_status_label(None) == "Missing"
    assert menu_handoff_problem_summary(["1 stale"]) == (
        "Fresh full backup needed before workstation handoff: 1 stale."
    )
    assert "prod→dev sync" in protection_handoff_action_item(include_sync=True)


def test_print_backup_status_report_uses_display_labels(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.status import BackupStatusEntry, BackupStatusReport
    from mercury.backup.terminal.status import print_backup_status_report

    report = BackupStatusReport(
        backup_root=str(tmp_path / "backups"),
        backup_root_state="usb-mounted",
        source_count=1,
        verified_count=1,
        missing_count=0,
        failed_count=0,
        stale_count=1,
        unknown_freshness_count=0,
        entries=[
            BackupStatusEntry(
                database="erebus_threat_intel_prod",
                role="prod",
                protection_status="verified",
                backup_id="erebus-full-1",
                backup_directory=str(tmp_path / "backups" / "erebus"),
                backup_created_at="2026-06-08T12:00:00+00:00",
                freshness="stale",
                backup_age="2d ago",
                recommend_full_backup=True,
                issues=[],
            )
        ],
        warnings=[],
    )
    print_backup_status_report(report)
    out = capsys.readouterr().out
    assert "Verified" in out
    assert "Stale" in out
    assert "VERIFY" in out
    assert "Production databases" in out
    assert "handoff should wait for fresh full backups" in out
    assert "Artifact verified means backup files pass checksum" in out


def test_backup_status_includes_restore_check_and_phase3b_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mercury.backup.status import build_backup_status_report
    from mercury.core.execution_policy import ExecutionPolicy

    monkeypatch.setattr(
        "mercury.backup.status.select_batch_sources",
        lambda **kwargs: ["erebus_threat_intel_prod"],
    )
    monkeypatch.setattr(
        "mercury.backup.status.find_latest_backup_directory",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "mercury.backup.status._live_server_database_names",
        lambda **kwargs: {"erebus_threat_intel_prod"},
    )
    monkeypatch.setattr(
        "mercury.backup.status.latest_restore_check_status_by_database",
        lambda: {"erebus_threat_intel_prod": "passed"},
    )
    monkeypatch.setattr(
        "mercury.backup.status.sealed_phase3b_package_note",
        lambda: "Sealed Phase 3B rehearsal package present (20260722T055400Z_phase3b).",
    )
    report = build_backup_status_report(
        live=False,
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path / "backups",
            config_path=None,
            allow_unsafe_backup_root=True,
        ),
    )
    assert report.entries[0].restore_check_status == "passed"
    assert any("Phase 3B" in warning for warning in report.warnings)


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

"""Tests for read-only restore readiness / target completeness checks."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from mercury.backup.checksum import write_checksum_file
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.inspect import DatabaseInspectResult
from mercury.restore.check_plan import build_restore_check_plan
from mercury.restore.readiness import (
    EREBUS_CANONICAL_TABLES,
    build_target_completeness_entry,
    build_target_completeness_report,
    parse_schema_artifact,
)

from tests.conftest import run_cli


def _write_verified_backup(
    tmp_path: Path,
    *,
    database: str,
    table_names: list[str],
    view_count: int = 76,
) -> Path:
    backup_dir = tmp_path / "2026-06-09" / database
    backup_dir.mkdir(parents=True)
    dump_name = f"{database}_20260609_030000_000.sql.gz"
    schema_name = f"{database}_20260609_030000_000.schema.sql.gz"
    dump_path = backup_dir / dump_name
    schema_path = backup_dir / schema_name
    dump_path.write_bytes(b"backup-data\n")
    lines = [f"CREATE TABLE `{name}` (\n  id INT\n);\n" for name in table_names]
    lines.extend(f"CREATE VIEW `{database}_view_{index}` AS SELECT 1;\n" for index in range(view_count))
    with gzip.open(schema_path, "wt", encoding="utf-8") as handle:
        handle.writelines(lines)
    write_checksum_file(backup_dir, [dump_name, schema_name])
    manifest = {
        "backup_id": f"{database}-full-20260609_030000_000",
        "database": database,
        "backup_kind": BACKUP_KIND_FULL,
        "created_at": "2026-06-09T03:00:00+00:00",
        "dump_file": dump_name,
        "schema_file": schema_name,
        "sha256": "",
        "schema_sha256": "",
        "size_bytes": dump_path.stat().st_size,
        "schema_size_bytes": schema_path.stat().st_size,
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return backup_dir


def test_parse_schema_artifact_counts_tables_and_views(tmp_path: Path) -> None:
    artifact = tmp_path / "schema.sql.gz"
    with gzip.open(artifact, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `alpha` (\n  id INT\n);\n")
        handle.write("CREATE TABLE `beta` (\n  id INT\n);\n")
        handle.write("CREATE VIEW `v1` AS SELECT 1;\n")
    tables, views, names = parse_schema_artifact(artifact)
    assert tables == 2
    assert views == 1
    assert names == {"alpha", "beta"}


def test_target_completeness_flags_neptune_incomplete_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_names = list(EREBUS_CANONICAL_TABLES) + [
        f"table_{index:03d}" for index in range(125 - len(EREBUS_CANONICAL_TABLES))
    ]
    _write_verified_backup(
        tmp_path,
        database="erebus_threat_intel_prod",
        table_names=table_names,
        view_count=76,
    )
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
            table_count=20,
            view_count=6,
            total_bytes=1024,
        )

    def fake_scalars(_config, _sql) -> list[str]:
        return [f"table_{index:03d}" for index in range(20)]

    entry = build_target_completeness_entry(
        "erebus_threat_intel_prod",
        live=True,
        config=object(),
        inspect_fn=fake_inspect,
        scalars_fn=fake_scalars,
    )

    assert entry.completeness_status == "incomplete"
    assert entry.live_object_count == 26
    assert entry.backup_object_count == 201
    assert entry.ready_for_restore_planning is False
    assert any("table count (20)" in blocker for blocker in entry.blockers)
    assert any("view count (6)" in blocker for blocker in entry.blockers)
    assert entry.missing_critical_tables
    assert any("severely incomplete" in warning for warning in entry.warnings)


def test_target_completeness_complete_when_counts_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_names = list(EREBUS_CANONICAL_TABLES) + [f"extra_{index}" for index in range(120)]
    _write_verified_backup(
        tmp_path,
        database="erebus_threat_intel_prod",
        table_names=table_names[:125],
        view_count=76,
    )
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

    def fake_scalars(_config, _sql) -> list[str]:
        return table_names[:125]

    entry = build_target_completeness_entry(
        "erebus_threat_intel_prod",
        live=True,
        config=object(),
        inspect_fn=fake_inspect,
        scalars_fn=fake_scalars,
    )
    assert entry.completeness_status == "complete"
    assert entry.ready_for_restore_planning is True
    assert not entry.blockers


def test_restore_check_plan_includes_target_completeness_without_blocking_restore_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_names = [f"table_{index:03d}" for index in range(125)]
    _write_verified_backup(
        tmp_path,
        database="erebus_threat_intel_prod",
        table_names=table_names,
        view_count=76,
    )
    monkeypatch.setattr(
        "mercury.restore.check_plan.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path,
            allow_unsafe_backup_root=True,
        ),
    )
    monkeypatch.setattr("mercury.restore.check_plan.should_probe_database_status", lambda: True)
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
            table_count=20,
            view_count=6,
            total_bytes=1024,
        )

    monkeypatch.setattr(
        "mercury.restore.readiness.inspect_database_on_server",
        fake_inspect,
    )
    monkeypatch.setattr(
        "mercury.restore.readiness.fetch_live_base_table_names",
        lambda *_args, **_kwargs: [f"table_{index:03d}" for index in range(20)],
    )

    plan = build_restore_check_plan("erebus_threat_intel_prod")
    assert plan.allowed is True
    assert plan.target_completeness is not None
    assert plan.target_completeness.completeness_status == "incomplete"


def test_build_target_completeness_report_demo_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.restore.readiness.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=Path("/tmp/mercury-empty-restore-readiness"),
            allow_unsafe_backup_root=True,
        ),
    )
    monkeypatch.setattr("mercury.restore.readiness.should_probe_database_status", lambda: False)
    report = build_target_completeness_report(live=False)
    assert report.entries
    assert report.unknown_count >= 1


def test_cli_restore_check_readiness_describes_schema_not_freshness(tmp_path: Path) -> None:
    table_names = list(EREBUS_CANONICAL_TABLES) + [
        f"table_{index:03d}" for index in range(125 - len(EREBUS_CANONICAL_TABLES))
    ]
    _write_verified_backup(
        tmp_path,
        database="erebus_threat_intel_prod",
        table_names=table_names,
        view_count=76,
    )
    env = {
        "MERCURY_BACKUP_ROOT": str(tmp_path),
        "MERCURY_ALLOW_UNSAFE_BACKUP_ROOT": "1",
    }
    result = run_cli(
        "restore-check",
        "readiness",
        "--demo",
        "--db",
        "erebus_threat_intel_prod",
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not backup data freshness" in result.stdout.lower() or "schema" in result.stdout.lower()


def test_restore_readiness_should_fail_on_live_backup_unavailable() -> None:
    from mercury.restore.readiness import TargetCompletenessEntry, TargetCompletenessReport, restore_readiness_should_fail

    report = TargetCompletenessReport(
        mode="live",
        backup_root="/tmp/mercury-empty",
        entries=[
            TargetCompletenessEntry(
                database="erebus_threat_intel_prod",
                completeness_status="backup_unavailable",
            )
        ],
        complete_count=0,
        incomplete_count=0,
        unknown_count=1,
    )
    assert restore_readiness_should_fail(report, live=True) is True
    assert restore_readiness_should_fail(report, live=False) is False


def test_cli_restore_check_readiness_fails_when_live_backup_missing(tmp_path: Path) -> None:
    env = {
        "MERCURY_BACKUP_ROOT": str(tmp_path),
        "MERCURY_ALLOW_UNSAFE_BACKUP_ROOT": "1",
    }
    result = run_cli(
        "restore-check",
        "readiness",
        "--db",
        "erebus_threat_intel_prod",
        env=env,
    )
    assert result.returncode == 1, result.stdout + result.stderr

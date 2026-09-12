"""Permission Intel prod→dev pair, order, rewrite, and isolation tests."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from mercury.backup.backup_runner import BackupExecutionError
from mercury.backup.content_contract import extract_restore_requirements
from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.core.scope import (
    ACTIVE_DEV_TARGET_DATABASES,
    is_active_dev_target,
    is_active_sync_pair,
    is_active_sync_source,
)
from mercury.database.prod_dev_pairs import (
    APPROVED_SYNC_PAIRS,
    build_prod_dev_pairs,
    prod_to_dev_name,
    schema_rewrites_for_pair,
)
from mercury.restore.restore_runner import assert_safe_restore_target, execute_restore_into_database
from mercury.sync.isolation import scan_sql_isolation
from mercury.sync.readiness import SyncReadinessEntry
from mercury.sync.selection import order_sync_entries, select_sync_entries
from mercury.sync.sync_plan import build_sync_plan_demo
from mercury.sync.sync_runner import run_sync_batch


def _policy(tmp_path: Path, *, live: bool = False) -> ExecutionPolicy:
    return ExecutionPolicy(
        dry_run=not live,
        live_actions_enabled=live,
        backup_root=tmp_path / "backups",
        config_path=None,
        allow_unsafe_backup_root=True,
    )


def _entry(
    source: str,
    target: str,
    *,
    ready: bool = True,
    project: str | None = None,
    depends_on: list[str] | None = None,
    backup_dir: str | None = None,
) -> SyncReadinessEntry:
    spec = next((item for item in APPROVED_SYNC_PAIRS if item.source == source), None)
    return SyncReadinessEntry(
        prod=source,
        expected_dev=target,
        dev_listed=True,
        project=project or (spec.project if spec else None),
        latest_backup_dir=backup_dir,
        backup_verified=ready,
        ready_for_sync_planning=ready,
        blockers=[] if ready else ["No artifact-verified on-disk backup found for production source."],
        sync_order=spec.sync_order if spec else 99,
        depends_on_sources=list(depends_on if depends_on is not None else (spec.depends_on_sources if spec else ())),
    )


def test_permission_intel_is_an_official_sync_pair() -> None:
    assert prod_to_dev_name("android_permission_intel") == "android_permission_intel_dev"
    assert is_active_sync_pair("android_permission_intel", "android_permission_intel_dev")
    assert is_active_sync_source("android_permission_intel")
    assert is_active_dev_target("android_permission_intel_dev")
    assert "android_permission_intel_dev" in ACTIVE_DEV_TARGET_DATABASES
    assert schema_rewrites_for_pair("erebus_threat_intel_prod", "erebus_threat_intel_dev") == {
        "android_permission_intel": "android_permission_intel_dev",
    }


def test_permission_intel_pair_discovery_order() -> None:
    names = [
        "scytaledroid_core_prod",
        "scytaledroid_core_dev",
        "android_permission_intel",
        "android_permission_intel_dev",
        "erebus_threat_intel_prod",
        "erebus_threat_intel_dev",
    ]
    pairs = build_prod_dev_pairs(names)
    assert [pair.prod for pair in pairs] == [
        "android_permission_intel",
        "erebus_threat_intel_prod",
        "scytaledroid_core_prod",
    ]
    assert pairs[1].depends_on_sources == ["android_permission_intel"]
    assert pairs[2].depends_on_sources == ["android_permission_intel"]


def test_sync_plan_demo_includes_permission_intel() -> None:
    plan = build_sync_plan_demo()
    sources = [entry.source for entry in plan.entries]
    assert sources == [
        "android_permission_intel",
        "erebus_threat_intel_prod",
        "scytaledroid_core_prod",
    ]
    pi = plan.entries[0]
    assert pi.target == "android_permission_intel_dev"
    assert pi.target_present is True


def test_pi_dev_replacement_refuses_without_confirmation(tmp_path: Path) -> None:
    entry = _entry("android_permission_intel", "android_permission_intel_dev")
    result = run_sync_batch([entry], execute=True, policy=_policy(tmp_path, live=True))
    assert result.executed_count == 0
    assert result.refused_count == 1
    assert "SYNC DEV" in result.results[0].message


def test_pi_dev_replacement_cannot_target_production() -> None:
    with pytest.raises(BackupExecutionError, match="android_permission_intel"):
        assert_safe_restore_target("android_permission_intel")
    assert_safe_restore_target("android_permission_intel_dev")


def test_restore_refuses_permission_intel_production_target(tmp_path: Path) -> None:
    dump = tmp_path / "dump.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `t` (id int);\n")
    with pytest.raises(BackupExecutionError, match="production|android_permission_intel"):
        execute_restore_into_database(
            target_database="android_permission_intel",
            dump_path=dump,
            source_database="android_permission_intel",
            execute=False,
            policy=_policy(tmp_path),
        )


def test_dependency_order_is_not_alphabetical() -> None:
    entries = [
        _entry("scytaledroid_core_prod", "scytaledroid_core_dev"),
        _entry("erebus_threat_intel_prod", "erebus_threat_intel_dev"),
        _entry("android_permission_intel", "android_permission_intel_dev"),
    ]
    ordered = order_sync_entries(entries)
    assert [entry.prod for entry in ordered] == [
        "android_permission_intel",
        "erebus_threat_intel_prod",
        "scytaledroid_core_prod",
    ]
    selected = select_sync_entries(list(reversed(entries)))
    assert [entry.prod for entry in selected] == [
        "android_permission_intel",
        "erebus_threat_intel_prod",
        "scytaledroid_core_prod",
    ]


def test_dependent_sync_skipped_after_pi_failure(tmp_path: Path) -> None:
    entries = [
        _entry("android_permission_intel", "android_permission_intel_dev", ready=False),
        _entry("erebus_threat_intel_prod", "erebus_threat_intel_dev", ready=True),
        _entry("scytaledroid_core_prod", "scytaledroid_core_dev", ready=True),
    ]
    batch = run_sync_batch(entries, execute=False, policy=_policy(tmp_path))
    by_source = {item.source: item for item in batch.results}
    assert by_source["android_permission_intel"].refused is True
    assert "upstream refresh failed" in by_source["erebus_threat_intel_prod"].message
    assert "upstream refresh failed" in by_source["scytaledroid_core_prod"].message
    assert by_source["erebus_threat_intel_prod"].refused is True
    assert batch.executed_count == 0


def test_one_pair_erebus_does_not_require_pi_in_same_batch(tmp_path: Path) -> None:
    dump_dir = tmp_path / "erebus"
    dump_dir.mkdir()
    dump = dump_dir / "erebus.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `t` (id int);\n")
    (dump_dir / "manifest.json").write_text(
        json.dumps({"dump_file": dump.name, "sha256": "x"}), encoding="utf-8"
    )
    entry = _entry(
        "erebus_threat_intel_prod",
        "erebus_threat_intel_dev",
        backup_dir=str(dump_dir),
    )
    batch = run_sync_batch([entry], execute=False, policy=_policy(tmp_path))
    assert batch.results[0].source == "erebus_threat_intel_prod"
    assert batch.results[0].refused is False
    assert batch.results[0].dry_run is True


def test_erebus_external_pi_reference_objects(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write(
            "/*!50001 VIEW `permission_unknown_metrics` AS SELECT * FROM "
            "`android_permission_intel`.`android_permission_dict_unknown`; */\n"
        )
        handle.write(
            "CREATE PROCEDURE `p_local`() SELECT 1;\n"
        )
        handle.write("INSERT INTO note VALUES ('android_permission_intel.not_an_identifier');\n")
    contract = extract_restore_requirements(dump, source_database="erebus_threat_intel_prod")
    assert contract.external_schema_references == ["android_permission_intel"]
    assert contract.external_schema_objects[0].object_kind == "view"
    assert contract.external_schema_objects[0].object_name == "permission_unknown_metrics"


def test_approved_production_to_dev_schema_rewrite_preserves_strings_and_comments(tmp_path: Path) -> None:
    from mercury.database.mariadb.import_stream import run_compressed_sql_import

    dump_path = tmp_path / "erebus.sql.gz"
    payload = "\n".join(
        [
            "-- comment android_permission_intel.table stays",
            "CREATE VIEW `v` AS SELECT * FROM `android_permission_intel`.`android_permission_dict_unknown`;",
            "INSERT INTO note VALUES ('android_permission_intel.not_an_identifier');",
            "",
        ]
    )
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(payload)
    capture = tmp_path / "captured.sql"
    fake = tmp_path / "fake.sh"
    fake.write_text(f'#!/usr/bin/env bash\ncat > "{capture}"\n', encoding="utf-8")
    fake.chmod(0o755)
    run_compressed_sql_import(
        [str(fake)],
        {},
        dump_path,
        rewrite_database=("erebus_threat_intel_prod", "erebus_threat_intel_dev"),
        rewrite_databases=schema_rewrites_for_pair(
            "erebus_threat_intel_prod", "erebus_threat_intel_dev"
        ),
    )
    written = capture.read_text(encoding="utf-8")
    assert "`android_permission_intel_dev`.`android_permission_dict_unknown`" in written
    assert "'android_permission_intel.not_an_identifier'" in written
    assert "-- comment android_permission_intel.table stays" in written


def test_post_sync_production_reference_scan_fails_closed() -> None:
    report = scan_sql_isolation(
        [
            (
                "view",
                "v_bad",
                "SELECT * FROM `android_permission_intel`.`android_permission_dict_unknown`",
            )
        ],
        target="erebus_threat_intel_dev",
    )
    assert report.isolation == "FAIL"
    assert report.production_schema_references == 1
    assert "PRODUCTION_SCHEMA_REFERENCES=1" in report.format_receipt()


def test_post_sync_production_reference_scan_passes_on_dev_pi() -> None:
    report = scan_sql_isolation(
        [
            (
                "view",
                "v_ok",
                "SELECT * FROM `android_permission_intel_dev`.`android_permission_dict_unknown`",
            ),
            (
                "view",
                "v_note",
                "SELECT 'android_permission_intel.not_an_identifier'",
            ),
        ],
        target="erebus_threat_intel_dev",
    )
    assert report.isolation == "PASS"
    assert report.production_schema_references == 0
    assert report.dev_pi_references == 1
    assert report.format_receipt() == (
        "TARGET=erebus_threat_intel_dev\n"
        "PRODUCTION_SCHEMA_REFERENCES=0\n"
        "DEV_PI_REFERENCES=1\n"
        "ISOLATION=PASS"
    )


def test_erebus_and_scytale_flows_remain_valid() -> None:
    assert is_active_sync_pair("erebus_threat_intel_prod", "erebus_threat_intel_dev")
    assert is_active_sync_pair("scytaledroid_core_prod", "scytaledroid_core_dev")
    assert not is_active_sync_pair("obsidiandroid_core_prod", "obsidiandroid_core_dev")
    assert not is_active_sync_pair("erebus_threat_intel_prod", "scytaledroid_core_dev")

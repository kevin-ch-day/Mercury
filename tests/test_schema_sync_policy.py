"""Tests for schema plan, sync plan, dump planner, and policy validation."""

import json
from pathlib import Path

from mercury.backup.dump_planner import build_planned_dump
from mercury.database.policy import validate_config_policy
from mercury.backup.schema_plan import build_schema_backup_plan_demo
from mercury.sync.sync_plan import build_sync_plan_demo
from mercury.core.safety import BACKUP_KIND_SCHEMA_ONLY, SYNC_DEV_CONFIRMATION_PHRASE


def test_schema_plan_sources_only_backup_sources() -> None:
    plan = build_schema_backup_plan_demo()
    assert "erebus_threat_intel_prod" in plan.sources
    assert "erebus_threat_intel_dev" not in plan.sources


def test_sync_plan_requires_confirmation_phrase() -> None:
    plan = build_sync_plan_demo()
    assert plan.enabled is False
    assert plan.confirmation_phrase == SYNC_DEV_CONFIRMATION_PHRASE
    assert any(e.source == "erebus_threat_intel_prod" for e in plan.entries)


def test_sync_plan_all_catalog_pairs_have_dev() -> None:
    plan = build_sync_plan_demo()
    erebus = next(e for e in plan.entries if e.source == "erebus_threat_intel_prod")
    assert erebus.target_present is True
    assert erebus.blocked_reason is None


def test_planned_dump_schema_command() -> None:
    dump = build_planned_dump("erebus_threat_intel_prod", BACKUP_KIND_SCHEMA_ONLY)
    assert "erebus_threat_intel_prod" in dump.command
    assert dump.output_file.endswith(".schema.sql.gz")


def test_policy_validate_demo_ok() -> None:
    report = validate_config_policy(use_demo_catalog=True)
    assert report.databases_checked >= 5
    assert report.ok()


def test_sample_manifest_writes_json(tmp_path: Path, monkeypatch) -> None:
    from mercury.backup.sample_manifest import write_sample_manifests

    monkeypatch.setattr("mercury.backup.sample_manifest.OUTPUT_DIR", tmp_path)
    paths = write_sample_manifests(tmp_path)
    assert len(paths) == 2
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    assert data["database"] == "erebus_threat_intel_prod"
    assert data["verified"] is False

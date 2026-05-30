"""M4.5: verification plan, report preview, backup list."""

import json
import subprocess
import sys

import pytest

from mercury.backup_list import DEMO_BACKUP_RECORDS, build_demo_backup_list
from mercury.manifest_preview import ManifestPreviewError, build_manifest_preview
from mercury.report_preview import build_report_preview, format_report_preview_markdown
from mercury.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY
from mercury.verification import (
    BackupVerificationResult,
    apply_verification_success,
    build_demo_verification_result,
    build_verification_plan_demo,
)
from mercury.verify_display import print_verification_plan

FIXED_DATE = "2026-05-30"
FIXED_TS = "20260530_120000"


def test_verify_plan_demo_has_future_checks() -> None:
    plan = build_verification_plan_demo(date=FIXED_DATE, timestamp=FIXED_TS)
    assert len(plan.future_checks) >= 9
    assert plan.demo_results
    assert all(not r.verified for r in plan.demo_results)


def test_verify_plan_command_output(capsys: pytest.CaptureFixture[str]) -> None:
    print_verification_plan(build_verification_plan_demo(date=FIXED_DATE, timestamp=FIXED_TS))
    out = capsys.readouterr().out
    assert "BACKUP VERIFICATION PLAN" in out
    assert "Future checks:" in out
    assert "[1]" in out
    assert "dry-run only" in out


def test_report_preview_full_includes_sql_gz_and_dr_language() -> None:
    report = build_report_preview(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    text = format_report_preview_markdown(report)
    assert ".sql.gz" in text
    assert "disaster recovery" in text.lower() or "prod-to-dev" in text.lower()
    assert report.planned_dump_path is not None


def test_report_preview_schema_only_not_sufficient_for_dr() -> None:
    report = build_report_preview(
        "erebus_threat_intel_prod",
        BACKUP_KIND_SCHEMA_ONLY,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    text = format_report_preview_markdown(report)
    assert ".schema.sql.gz" in text
    assert "not sufficient" in text.lower()
    assert report.planned_schema_path is not None


def test_backup_list_demo_returns_four_records() -> None:
    demo = build_demo_backup_list(date=FIXED_DATE, timestamp=FIXED_TS)
    assert len(demo.records) == len(DEMO_BACKUP_RECORDS)
    assert demo.records[0].verified is False
    assert all(r.preview_only for r in demo.records)
    kinds = {(r.database, r.backup_kind) for r in demo.records}
    assert ("erebus_threat_intel_prod", BACKUP_KIND_FULL) in kinds
    assert ("scytaledroid_core_prod", BACKUP_KIND_SCHEMA_ONLY) in kinds


def test_verification_model_defaults_verified_false() -> None:
    preview = build_manifest_preview(
        "android_permission_intel",
        BACKUP_KIND_FULL,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    result = build_demo_verification_result(preview)
    assert result.verified is False
    assert result.checksum_matches is False
    assert len(result.issues) >= 1


def test_verification_model_can_represent_issues_and_success() -> None:
    preview = build_manifest_preview(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    failed = build_demo_verification_result(preview)
    assert failed.issues

    passed = apply_verification_success(failed)
    assert passed.verified is True
    assert passed.issues == []
    assert passed.checksum_matches is True


def test_report_preview_refuses_dev() -> None:
    with pytest.raises(ManifestPreviewError):
        build_report_preview("erebus_threat_intel_dev", BACKUP_KIND_FULL)


def test_manifest_preview_refuses_dev() -> None:
    with pytest.raises(ManifestPreviewError):
        build_manifest_preview("gecko_research_database_dev", BACKUP_KIND_SCHEMA_ONLY)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mercury.cli", *args],
        capture_output=True,
        text=True,
    )


def test_cli_verify_plan_demo() -> None:
    result = _run("backup", "verify-plan", "--demo")
    assert result.returncode == 0
    assert "BACKUP VERIFICATION PLAN" in result.stdout


def test_cli_backup_list_demo() -> None:
    result = _run("backup", "list", "--demo")
    assert result.returncode == 0
    assert "demo planned" in result.stdout.lower()
    assert "erebus_threat_intel_prod" in result.stdout


def test_cli_report_preview_full() -> None:
    result = _run(
        "report",
        "preview",
        "--db",
        "erebus_threat_intel_prod",
        "--kind",
        "full",
    )
    assert result.returncode == 0
    assert "Mercury Backup Report" in result.stdout
    assert ".sql.gz" in result.stdout

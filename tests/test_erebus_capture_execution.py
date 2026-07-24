"""Phase B execution-lock and production CLI regression coverage."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from mercury.cli import app
from mercury.migration.erebus_capture.context import CaptureContext
from mercury.migration.erebus_capture.service import execute_capture
from mercury.migration.erebus_capture.storage_preflight import (
    EXPECTED_LABEL, EXPECTED_MOUNT, EXPECTED_UUID, StorageFacts,
)
from mercury.migration.erebus_capture.full_suite_policy import ExpectedFailure, FullSuiteSummary, evaluate
from mercury.migration.erebus_capture.contract import REQUIRED, expected_bundle_name, validate_members


def _facts() -> StorageFacts:
    return StorageFacts("/dev/synthetic1", "/dev/synthetic", "ext4", EXPECTED_LABEL,
                        EXPECTED_UUID, EXPECTED_MOUNT, "rw", 1, True, True)


def test_default_context_refuses_before_preview_or_capture_paths_exist(tmp_path: Path) -> None:
    context = CaptureContext(tmp_path / "control", tmp_path / "repo", tmp_path / "receipt",
                             tmp_path / "phase", tmp_path / "intake", _facts)
    result = execute_capture(context, "preview-exact")
    assert result.classification == "EXECUTION_NOT_AUTHORIZED"
    assert not (tmp_path / "control" / "validation" / "erebus").exists()


def test_production_cli_has_no_synthetic_execution_bypass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    facts = tmp_path / "facts.json"; facts.write_text(json.dumps(_facts().__dict__))
    args = [
        "migration", "capture-erebus-source", "execute", "--preview-id", "preview-exact",
        "--repo", str(repo), "--recovery-receipt", str(tmp_path / "receipt"),
        "--phase3b-root", str(tmp_path / "phase"), "--intake-contract", str(tmp_path / "intake"),
        "--control-root", str(tmp_path / "control"), "--storage-facts", str(facts),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "CAPTURE EXECUTION REFUSED" in result.output
    assert "EXECUTION_NOT_AUTHORIZED" in result.output
    bypass = CliRunner().invoke(app, [*args, "--allow-synthetic-execution"])
    assert bypass.exit_code != 0
    assert not (tmp_path / "control" / "validation" / "erebus").exists()


def test_full_suite_policy_requires_exact_failure_identity_and_classification() -> None:
    approved = (ExpectedFailure("tests/example.py::test_known", "host_output"),)
    clean = FullSuiteSummary("pytest -q", 0, 10, 10, (), 0)
    assert evaluate(clean) == (True, "FULL_SUITE_PASS")
    exact = FullSuiteSummary("pytest -q", 1, 10, 9, approved, 0)
    assert evaluate(exact, approved) == (True, "FULL_SUITE_APPROVED_EXCEPTIONS")
    changed = FullSuiteSummary("pytest -q", 1, 10, 9, (ExpectedFailure("tests/other.py::test_new", "host_output"),), 0)
    assert evaluate(changed, approved) == (False, "FULL_SUITE_UNEXPECTED_FAILURES")
    assert evaluate(FullSuiteSummary("pytest -q", 1, 0, 0, (), 0, collection_errors=1)) == (False, "FULL_SUITE_STRUCTURAL_FAILURE")


def test_capture_contract_rejects_unexpected_and_historical_members() -> None:
    members = set(REQUIRED) | {expected_bundle_name("abcdef0")}
    assert validate_members(members, "abcdef0") == []
    assert "unexpected member: surprise.txt" in validate_members(members | {"surprise.txt"}, "abcdef0")
    assert "forbidden member: logs/run.txt" in validate_members(members | {"logs/run.txt"}, "abcdef0")

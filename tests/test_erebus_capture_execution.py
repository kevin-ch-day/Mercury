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
from mercury.migration.erebus_capture.validation_runner import DeterministicValidationRunner, ValidationResult
from mercury.migration.erebus_capture.contract import REQUIRED, expected_bundle_name, validate_members
from mercury.migration.erebus_capture.scanner import scan_capture
import pytest


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


@pytest.mark.parametrize("summary", [
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0),
    FullSuiteSummary("pytest -q", 0, 2, 1, (ExpectedFailure("x", "wrong"),), 0),
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0, interrupted=True),
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0, focused_failures=1),
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0, dependency_valid=False),
])
def test_full_suite_policy_refuses_inconsistent_or_incomplete_results(summary: FullSuiteSummary) -> None:
    assert not evaluate(summary)[0]


def test_full_suite_policy_refuses_missing_or_changed_approved_failure() -> None:
    approved = (ExpectedFailure("tests/a.py::test_a", "host_output"),)
    missing = FullSuiteSummary("pytest", 0, 1, 1, (), 0)
    # A zero-failure run is valid independently; a nonzero run missing the exact approved identity is not.
    assert evaluate(missing, approved)[0]
    changed = FullSuiteSummary("pytest", 1, 1, 0, (ExpectedFailure("tests/a.py::test_a", "different"),), 0)
    assert not evaluate(changed, approved)[0]


def test_validation_runner_result_is_structured_and_refuses_incomplete_execution(tmp_path: Path) -> None:
    result = ValidationResult(("pytest",), str(tmp_path), 1, stderr="failed", parsed={"failed": 1})
    runner = DeterministicValidationRunner({"focused_tests": result})
    recorded = runner.run("focused_tests", cwd=tmp_path, command=("ignored",))
    assert not recorded.accepted
    assert recorded.evidence()["parsed"] == {"failed": 1}


def test_capture_contract_rejects_unexpected_and_historical_members() -> None:
    members = set(REQUIRED) | {expected_bundle_name("abcdef0")}
    assert validate_members(members, "abcdef0") == []
    assert "unexpected member: surprise.txt" in validate_members(members | {"surprise.txt"}, "abcdef0")
    assert "forbidden member: logs/run.txt" in validate_members(members | {"logs/run.txt"}, "abcdef0")


@pytest.mark.parametrize("name,content", [
    (".env", "API_KEY=abcdefghijklmnop"), ("private.pem", "-----BEGIN RSA PRIVATE KEY-----"),
    ("private.txt", "-----BEGIN OPENSSH PRIVATE KEY-----"), ("private.txt", "-----BEGIN EC PRIVATE KEY-----"),
    ("data.db", "binary"), ("logs/run.txt", "x"), ("output/report.txt", "x"),
    ("ScytaleDroid/source.py", "x"), ("notes.txt", "token=abcdefghijklmnop"),
])
def test_scanner_rejects_governed_forbidden_content(tmp_path: Path, name: str, content: str) -> None:
    path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    assert scan_capture(tmp_path, short_sha="abcdef0")


def test_scanner_accepts_benign_hashes_and_docs_when_members_are_valid(tmp_path: Path) -> None:
    for member in set(REQUIRED) | {expected_bundle_name("abcdef0")}:
        path = tmp_path / member; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("documentation discusses tokens; sha256=" + "a" * 64 + "\n")
    assert scan_capture(tmp_path, short_sha="abcdef0") == []


def test_scanner_accepts_empty_env_template_and_refuses_symlink(tmp_path: Path) -> None:
    for member in set(REQUIRED) | {expected_bundle_name("abcdef0")}:
        path = tmp_path / member; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("ok\n")
    (tmp_path / ".env").write_text("")
    assert "forbidden path: .env" not in scan_capture(tmp_path, short_sha="abcdef0")
    # A symlink is rejected independently from its target content.
    target = tmp_path / "target.txt"; target.write_text("ok")
    link = tmp_path / "artifacts" / "intake_contract" / "link.txt"; link.parent.mkdir(parents=True); link.symlink_to(target)
    assert any(item.startswith("nonregular member:") for item in scan_capture(tmp_path, short_sha="abcdef0"))

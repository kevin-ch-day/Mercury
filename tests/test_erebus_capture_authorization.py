"""Authorization receipt and real-execution gate coverage (no live capture)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mercury.cli import app
from mercury.migration.erebus_capture.authorization import (
    CONFIRMATION,
    SCHEMA,
    load_execution_authorization,
)
from mercury.migration.erebus_capture.context import CaptureContext
from mercury.migration.erebus_capture.service import execute_capture
from mercury.migration.erebus_capture.validation_runner import (
    DeterministicValidationRunner,
    ValidationResult,
    parse_pytest_output,
)
from test_erebus_capture_execution import SyntheticCaptureFixture


def _auth_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "confirm": CONFIRMATION,
        "preview_id": "preview-a",
        "capture_id": "capture-a",
        "mercury_commit": "m" * 40,
        "erebus_commit": "e" * 40,
        "authorized_at_utc": "2026-07-24T19:00:00Z",
        "expires_at_utc": "2099-01-01T00:00:00Z",
        "approved_full_suite_failures": [],
    }
    payload.update(overrides)
    return payload


def test_authorization_loader_accepts_exact_pins(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(_auth_payload()) + "\n")
    auth = load_execution_authorization(
        path,
        preview_id="preview-a",
        capture_id="capture-a",
        mercury_commit="m" * 40,
        erebus_commit="e" * 40,
    )
    assert auth.preview_id == "preview-a"
    assert auth.sha256


def test_authorization_loader_refuses_mismatch_expiry_and_confirm(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(_auth_payload(confirm="NOPE")) + "\n")
    with pytest.raises(ValueError, match="AUTHORIZATION_CONFIRM"):
        load_execution_authorization(path, preview_id="preview-a")

    path.write_text(json.dumps(_auth_payload()) + "\n")
    with pytest.raises(ValueError, match="AUTHORIZATION_PREVIEW_MISMATCH"):
        load_execution_authorization(path, preview_id="other")

    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(_auth_payload(expires_at_utc=expired)) + "\n")
    with pytest.raises(ValueError, match="AUTHORIZATION_EXPIRED"):
        load_execution_authorization(path, preview_id="preview-a")


def test_parse_pytest_output_extracts_counts_and_failures() -> None:
    text = "FAILED tests/example.py::test_one\n======= 1 failed, 9 passed, 2 skipped in 1.00s =======\n"
    parsed = parse_pytest_output(text)
    assert parsed["passed_count"] == 9
    assert parsed["failed_count"] == 1
    assert parsed["skipped_count"] == 2
    assert parsed["failures"][0]["node_id"] == "tests/example.py::test_one"


def test_execute_remains_locked_without_authorization(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path, allow_synthetic_execution=False)
    fixture.publish_ready()
    locked = CaptureContext(
        fixture.control, fixture.repo, fixture.receipt, fixture.phase, fixture.intake,
        fixture.context.storage_resolver,
    )
    result = execute_capture(locked, fixture.preview_id)
    assert not result.ok
    assert result.classification == "EXECUTION_NOT_AUTHORIZED"


def test_execute_accepts_real_authorization_with_injected_runner(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path, allow_synthetic_execution=False)
    fixture.publish_ready()
    runner = DeterministicValidationRunner(
        {
            "full_suite": ValidationResult(
                ("python", "-m", "pytest", "-q"),
                str(fixture.repo),
                0,
                parsed={"collected_count": 1, "passed_count": 1, "skipped_count": 0, "failures": []},
            )
        }
    )
    context = CaptureContext(
        fixture.control, fixture.repo, fixture.receipt, fixture.phase, fixture.intake,
        fixture.context.storage_resolver,
        allow_real_execution=True,
        authorized_preview_id=fixture.preview_id,
        approved_full_suite_failures=(),
        validation_runner=runner,
        authorization_receipt_sha256="abc",
        git_runner=fixture.context.git_runner,
    )
    result = execute_capture(context, fixture.preview_id)
    assert result.ok, result.errors
    summary = json.loads(
        (fixture.control / "validation" / "erebus" / fixture.capture_id / "capture_summary.json").read_text()
    )
    assert summary["real_execution"] is True
    assert summary["status"] == "CAPTURE_VERIFIED"


def test_cli_execute_without_receipt_stays_locked(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    facts = tmp_path / "facts.json"
    facts.write_text(
        json.dumps(
            {
                "partition": "/dev/x1",
                "parent": "/dev/x",
                "fstype": "ext4",
                "label": "MERCURY_DATA_V2",
                "uuid": "715f29a9-2671-477b-8c8d-515d190addb9",
                "mount_path": "/mnt/MERCURY_DATA_V2",
                "mount_options": "rw",
                "free_bytes": 100,
                "source_host": True,
                "writer_enabled": True,
                "active_operations": [],
                "ambiguous": False,
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "migration", "capture-erebus-source", "execute",
            "--preview-id", fixture.preview_id,
            "--repo", str(fixture.repo),
            "--recovery-receipt", str(fixture.receipt),
            "--phase3b-root", str(fixture.phase),
            "--intake-contract", str(fixture.intake),
            "--control-root", str(fixture.control),
            "--storage-facts", str(facts),
        ],
    )
    assert result.exit_code != 0
    assert "EXECUTION_NOT_AUTHORIZED" in result.stdout


def test_cli_review_preview_is_read_only(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "migration", "capture-erebus-source", "review-preview",
            "--preview-id", fixture.preview_id,
            "--control-root", str(fixture.control),
        ],
    )
    assert result.exit_code == 0
    assert "PREVIEW READY" in result.stdout
    assert not (fixture.control / "validation" / "erebus" / fixture.capture_id).exists()

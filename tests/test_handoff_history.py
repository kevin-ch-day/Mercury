"""Tests for handoff history reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.handoff.history import build_handoff_history
from mercury.handoff.wizard import HandoffWizardPhaseResult, HandoffWizardResult
from mercury.state.ledger import (
    read_operator_database_bundle_rows,
    read_operator_operation_rows,
    read_operator_transfer_package_rows,
    record_handoff_wizard_run,
)


def test_build_handoff_history_merges_transfer_and_bundle_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "mercury_state_history"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "transfer_packages.csv").write_text(
        "timestamp,manifest_path,runbook_path,database_sources,verified_sources,repo_count,"
        "dirty_repo_count,sync_ready,sync_blocked,actual_sync_state,handoff_status,"
        "database_package,repository_package,stale_source_count,warnings\n"
        "2026-07-02T10:00:00+00:00,/mnt/MERCURY_DATA_USB/manifests/transfer_manifest_1.json,"
        "/mnt/MERCURY_DATA_USB/runbooks/transfer_runbook_1.md,4,3,2,0,0,2,deferred,partial,partial,complete,1,\n",
        encoding="utf-8",
    )
    (state_root / "database_bundles.csv").write_text(
        "timestamp,index_manifest_path,index_runbook_path,source_count,verified_count,"
        "missing_count,failed_count,stale_count,unknown_freshness_count,package_status,warnings\n"
        "2026-07-02T09:00:00+00:00,/mnt/MERCURY_DATA_USB/manifests/database_transfer_manifest_1.json,"
        "/mnt/MERCURY_DATA_USB/runbooks/database_transfer_runbook_1.md,4,3,1,0,1,0,partial,stale\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mercury.handoff.history.read_operator_transfer_package_rows",
        lambda **kwargs: read_operator_transfer_package_rows(state_root=state_root),
    )
    monkeypatch.setattr(
        "mercury.handoff.history.read_operator_database_bundle_rows",
        lambda **kwargs: read_operator_database_bundle_rows(state_root=state_root),
    )
    monkeypatch.setattr(
        "mercury.handoff.history.read_operator_operation_rows",
        lambda **kwargs: read_operator_operation_rows(state_root=state_root),
    )

    report = build_handoff_history(limit=5)
    assert report.transfer_package_count == 1
    assert report.database_bundle_count == 1
    assert len(report.entries) == 2
    assert report.entries[0].event == "transfer package"
    assert report.entries[0].handoff_status == "partial"


def test_record_handoff_wizard_run_appends_operation(tmp_path: Path) -> None:
    state_root = tmp_path / "mercury_state"
    result = HandoffWizardResult(
        phases=[
            HandoffWizardPhaseResult(phase="verify", status="ok", summary="verified"),
        ],
        final_handoff_status="partial",
        cancelled=False,
    )
    record_handoff_wizard_run(result, state_root=state_root)
    lines = (state_root / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"event_type": "handoff_wizard_run"' in lines[0]
    assert '"final_handoff_status": "partial"' in lines[0]

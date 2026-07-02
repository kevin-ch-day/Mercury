"""Tests for guided handoff wizard."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.handoff.wizard import (
    HandoffWizardPhaseResult,
    resolve_wizard_phase_range,
    run_guided_handoff_wizard,
    run_handoff_backup_phase,
    sources_needing_backup,
)


def test_sources_needing_backup_includes_stale_and_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.backup.status import BackupStatusEntry, BackupStatusReport

    monkeypatch.setattr(
        "mercury.handoff.wizard.build_backup_status_report",
        lambda **kwargs: BackupStatusReport(
            backup_root="/tmp/usb",
            backup_root_state="usb-mounted",
            source_count=3,
            entries=[
                BackupStatusEntry(
                    database="fresh_db",
                    role="prod",
                    protection_status="verified",
                    freshness="fresh",
                ),
                BackupStatusEntry(
                    database="stale_db",
                    role="prod",
                    protection_status="verified",
                    freshness="stale",
                    recommend_full_backup=True,
                ),
                BackupStatusEntry(
                    database="missing_db",
                    role="prod",
                    protection_status="missing",
                ),
            ],
        ),
    )
    names = sources_needing_backup(live=True)
    assert names == ["stale_db", "missing_db"]


def test_run_handoff_backup_phase_skips_when_nothing_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mercury.handoff.wizard.sources_needing_backup", lambda **kwargs: [])
    result = run_handoff_backup_phase(live=True, execute=True)
    assert result.status == "skipped"
    assert "not needed" in result.summary.lower()


def test_run_guided_handoff_wizard_runs_phases_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _phase(name: str):
        def _runner(**kwargs):
            calls.append(name)
            return HandoffWizardPhaseResult(phase=name, status="ok", summary=f"{name} done")

        return _runner

    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_backup_phase", _phase("backup"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_verify_phase", _phase("verify"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_repo_bundle_phase", _phase("repo_bundle"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_db_bundle_phase", _phase("db_bundle"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_transfer_phase", _phase("transfer"))
    monkeypatch.setattr(
        "mercury.handoff.wizard.build_handoff_checklist",
        lambda **kwargs: type("C", (), {"handoff_status": "complete"})(),
    )
    monkeypatch.setattr("mercury.state.ledger.record_handoff_wizard_run", lambda *args, **kwargs: None)
    monkeypatch.setattr("mercury.handoff.snapshot.clear_handoff_snapshot", lambda: None)

    result = run_guided_handoff_wizard(live=False, execute=True)
    assert calls == ["backup", "verify", "repo_bundle", "db_bundle", "transfer"]
    assert result.final_handoff_status == "complete"


def test_resolve_wizard_phase_range_supports_resume_window() -> None:
    assert resolve_wizard_phase_range(start_phase="verify", end_phase="db_bundle") == [
        "verify",
        "repo_bundle",
        "db_bundle",
    ]


def test_resolve_wizard_phase_range_rejects_reversed_window() -> None:
    with pytest.raises(ValueError):
        resolve_wizard_phase_range(start_phase="transfer", end_phase="backup")


def test_run_guided_handoff_wizard_stops_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_backup_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="backup",
            status="failed",
            summary="backup failed",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_verify_phase",
        lambda **kwargs: pytest.fail("verify should not run after backup failure"),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.build_handoff_checklist",
        lambda **kwargs: type("C", (), {"handoff_status": "partial"})(),
    )

    result = run_guided_handoff_wizard(live=False, execute=True)
    assert len(result.phases) == 1
    assert result.phases[0].status == "failed"


def test_run_guided_handoff_wizard_can_resume_from_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _phase(name: str):
        def _runner(**kwargs):
            calls.append(name)
            return HandoffWizardPhaseResult(phase=name, status="ok", summary=f"{name} done")

        return _runner

    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_backup_phase", _phase("backup"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_verify_phase", _phase("verify"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_repo_bundle_phase", _phase("repo_bundle"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_db_bundle_phase", _phase("db_bundle"))
    monkeypatch.setattr("mercury.handoff.wizard.run_handoff_transfer_phase", _phase("transfer"))
    monkeypatch.setattr(
        "mercury.handoff.wizard.build_handoff_checklist",
        lambda **kwargs: type("C", (), {"handoff_status": "complete"})(),
    )

    result = run_guided_handoff_wizard(
        live=False,
        execute=False,
        start_phase="verify",
        end_phase="db_bundle",
    )
    assert calls == ["verify", "repo_bundle", "db_bundle"]
    assert len(result.phases) == 3


def test_cli_transfer_handoff_run_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.conftest import run_cli

    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_backup_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="backup",
            status="skipped",
            summary="Would run full backup for 0 source(s).",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_verify_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="verify",
            status="skipped",
            summary="Would verify all backup sources and update manifests.",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_repo_bundle_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="repo_bundle",
            status="skipped",
            summary="No repository entries configured for bundling.",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_db_bundle_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="db_bundle",
            status="skipped",
            summary="Would write database bundle index (package: empty).",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_transfer_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="transfer",
            status="skipped",
            summary="Would write combined transfer package (handoff: empty).",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.build_handoff_checklist",
        lambda **kwargs: type("C", (), {"handoff_status": "partial"})(),
    )
    result = run_cli("transfer", "handoff", "--run", "--seed", env={"MERCURY_BACKUP_ROOT": str(tmp_path)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Handoff wizard progress" in result.stdout


def test_cli_transfer_handoff_run_from_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_verify_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="verify",
            status="skipped",
            summary="Would verify all backup sources and update manifests.",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_repo_bundle_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="repo_bundle",
            status="skipped",
            summary="Would write repository bundles to USB.",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.run_handoff_db_bundle_phase",
        lambda **kwargs: HandoffWizardPhaseResult(
            phase="db_bundle",
            status="skipped",
            summary="Would write database bundle index (package: empty).",
        ),
    )
    monkeypatch.setattr(
        "mercury.handoff.wizard.build_handoff_checklist",
        lambda **kwargs: type("C", (), {"handoff_status": "partial"})(),
    )
    from tests.conftest import run_cli

    result = run_cli(
        "transfer",
        "handoff",
        "--run",
        "--seed",
        "--from-phase",
        "verify",
        "--through-phase",
        "db_bundle",
        env={"MERCURY_BACKUP_ROOT": str(tmp_path)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verify all source backups" in result.stdout
    assert "Write combined transfer package" not in result.stdout

"""Destination-move hub, labels, cancel UX, and package-pinned guides (hermetic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.menu.destination_move import (
    HUB_RECEIVER_GUIDE,
    HUB_REVIEW_PACKAGE,
    HUB_SAFE_DISCONNECT,
    build_destination_hub_options,
    build_destination_move_status,
    destination_move_action_label,
    destination_progress_label,
    receiver_guide_lines_for_package,
)
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance
from mercury.storage.retention import RetentionPolicy


@pytest.fixture
def host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    monkeypatch.setenv("MERCURY_TRANSITION_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("MERCURY_TEST_ISOLATION", "1")
    return path


def _verified_source_not_started(**extra) -> HostMaintenanceState:
    base = dict(
        storage_availability="detaching",
        writes_allowed=False,
        active_write_role="none",
        source_detach_preparation=True,
        destination_rehearsal_active=True,
        destination_rehearsal_in_progress=True,
        destination_rehearsal_planned=True,
        package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        package_id="destination_rehearsal_final_source_20260723T161213Z",
    )
    base.update(extra)
    return HostMaintenanceState(**base)


def test_prepare_destination_move_when_not_started(host_path: Path) -> None:
    save_host_maintenance(_verified_source_not_started(), path=host_path)
    assert destination_progress_label() == "Not started"
    assert destination_move_action_label() == "Prepare destination move"


def test_registered_destination_host_label(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=False,
            destination_rehearsal_planned=True,
            destination_rehearsal_active=False,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    assert destination_progress_label() == "Registered · validation not started"
    assert destination_move_action_label() == "Continue destination validation"


def test_validation_active_label(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=False,
            destination_rehearsal_planned=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    assert destination_progress_label() == "Validation active"
    assert destination_move_action_label() == "Continue destination validation"


def test_validation_passed_label(host_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_host_maintenance(_verified_source_not_started(), path=host_path)
    monkeypatch.setattr(
        "mercury.menu.destination_move.load_retention_policy",
        lambda: RetentionPolicy(destination_validation_pending=False),
    )
    assert destination_progress_label() == "Validation passed"
    assert destination_move_action_label() == "Continue destination validation"


def test_source_changed_status(host_path: Path) -> None:
    save_host_maintenance(
        _verified_source_not_started(source_data_changed_since_package=True),
        path=host_path,
    )
    status = build_destination_move_status(manifest={})
    assert "changes since package" in status.source_state.lower()


def test_package_missing_or_failed(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="",
            package_id="",
        ),
        path=host_path,
    )
    status = build_destination_move_status(manifest={})
    assert status.package_status == "Missing"

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            package_verification_status="FAILED",
            package_id="broken_pkg",
        ),
        path=host_path,
    )
    status = build_destination_move_status(manifest={})
    assert status.package_status == "Not verified"


def test_safe_disconnect_is_option_one_when_appropriate(host_path: Path) -> None:
    save_host_maintenance(_verified_source_not_started(), path=host_path)
    options = build_destination_hub_options()
    assert options[0][0] == "1"
    assert options[0][2] == HUB_SAFE_DISCONNECT
    assert "recommended" in options[0][1]
    assert options[1][2] == HUB_REVIEW_PACKAGE
    assert options[2][2] == HUB_RECEIVER_GUIDE


def test_safe_disconnect_not_first_when_validation_active(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=False,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    options = build_destination_hub_options()
    assert options[0][2] != HUB_SAFE_DISCONNECT
    assert HUB_SAFE_DISCONNECT in {a for _k, _l, a in options}


def test_current_package_identities_in_status(host_path: Path) -> None:
    pkg = "destination_rehearsal_final_source_20260723T161213Z"
    save_host_maintenance(_verified_source_not_started(package_id=pkg), path=host_path)
    status = build_destination_move_status(
        manifest={
            "mercury_commit": "abcdef1234567890",
            "mercury_capture_id": "mercury_capture_1",
            "erebus_commit": "3f1bb5bdeadbeef",
            "erebus_capture_id": "erebus_destination_candidate_1",
            "included_backup_ids": ["phase3b_backup_a", "phase3b_backup_b"],
        }
    )
    assert status.package_id == pkg
    assert status.package_status == "VERIFIED"
    assert "abcdef1" in status.mercury_line
    assert "3f1bb5b" in status.erebus_line
    assert status.databases_line == "Phase 3B dumps verified"
    assert status.destination_state == "Not started"
    assert "Safely disconnect" in status.recommended
    assert status.phase3b_backup_ids == ("phase3b_backup_a", "phase3b_backup_b")


def test_hub_hides_write_blocked_package_build_actions(host_path: Path) -> None:
    save_host_maintenance(_verified_source_not_started(), path=host_path)
    labels = " ".join(label for _k, label, _a in build_destination_hub_options())
    assert "Build Migration Package" not in labels
    assert "Capture Web Worktrees" not in labels
    assert "Advanced handoff tools" in labels


def test_receiver_guide_pinned_to_package_id() -> None:
    pkg = "destination_rehearsal_final_source_20260723T161213Z"
    lines = "\n".join(receiver_guide_lines_for_package(pkg))
    assert f"  {pkg}" in lines
    assert "Attach Mercury HDD" in lines
    assert "Verify package checksums" in lines
    assert "latest package" not in lines.lower()


def test_operator_datetime_is_local_not_bare_utc() -> None:
    from mercury.terminal.format import format_operator_datetime, format_package_id_snapshot

    text = format_package_id_snapshot("destination_rehearsal_final_source_20260723T161213Z")
    assert text is not None
    assert "UTC" not in text or "CDT" in text or "CST" in text or "·" in text
    assert "July 23, 2026" in text
    # Shared formatter accepts ISO UTC and yields local prose.
    local = format_operator_datetime("2026-07-23T16:12:00Z")
    assert "July 23, 2026" in local
    assert "·" in local


def test_exact_confirmation_cancel_prints_one_message(
    host_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from mercury.backup import session_wizard as wiz
    from mercury.storage.operation_availability import (
        AvailabilityClassification,
        OperationAvailability,
        OperationStatus,
    )
    from mercury.storage.transitions import TransitionStatus

    save_host_maintenance(_verified_source_not_started(), path=host_path)

    def _cancel_ensure(**_kwargs):
        return OperationAvailability(
            operation="database_backup",
            classification=AvailabilityClassification.STRONG_CONFIRMATION,
            available=False,
            operation_status=OperationStatus.CANCELLED,
            transition_status=TransitionStatus.CANCELLED,
            blockers=("confirmation phrase mismatch",),
        )

    monkeypatch.setattr(wiz, "assess_operation_availability", lambda *_a, **_k: type(
        "A",
        (),
        {
            "is_hard_block": False,
            "available": False,
            "classification": type("C", (), {"value": "STRONG_CONFIRMATION"})(),
        },
    )())
    monkeypatch.setattr(wiz, "_print_overview", lambda **_k: None)
    monkeypatch.setattr(wiz, "_choice_menu", lambda: "1")
    monkeypatch.setattr(wiz, "ensure_backup_writes_available", _cancel_ensure)
    monkeypatch.setattr(
        "mercury.backup.session_wizard.ensure_backup_writes_available",
        _cancel_ensure,
    )

    printed: list[str] = []
    monkeypatch.setattr(
        wiz.display_screen,
        "write_summary",
        lambda msg="": printed.append(str(msg)),
    )
    assert wiz.run_backup_sync_wizard() is None
    cancel_lines = [line for line in printed if "cancelled" in line.lower() or "writes remain" in line.lower()]
    assert cancel_lines == [
        "Backup and Sync cancelled.",
        "Mercury writes remain disabled.",
    ]
    out = capsys.readouterr().out
    assert "Backup cancelled." not in out


def test_startup_intent_uses_prepare_destination_move(host_path: Path) -> None:
    from mercury.menu.intent import INTENT_DESTINATION_REHEARSAL, build_startup_intent_options

    save_host_maintenance(_verified_source_not_started(), path=host_path)
    opts = build_startup_intent_options()
    dest = next(label for _k, label, action in opts if action == INTENT_DESTINATION_REHEARSAL)
    assert "Prepare destination move" in dest
    assert "Continue destination rehearsal" not in dest


def test_symbolic_hub_numbering_stays_synchronized(host_path: Path) -> None:
    save_host_maintenance(_verified_source_not_started(), path=host_path)
    options = build_destination_hub_options()
    keys = [k for k, _l, _a in options]
    assert keys == [str(i) for i in range(1, len(options) + 1)]
    actions = [a for _k, _l, a in options]
    assert len(actions) == len(set(actions))

"""Safe Disconnect UX: two-stage y/N, completion screen, intentional-detach state."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mercury.storage.detach_wizard import (
    DETACH_CONFIRMATION,
    HDD_POWERED_OFF_SAFE_TO_DISCONNECT,
    detach_execute_approved,
    format_disconnect_complete,
    format_privileged_detach_report,
    format_wizard_report,
)
from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    intentional_safe_disconnect_active,
    mark_detached,
    save_host_maintenance,
)
from mercury.storage.transitions import RESTORE_SOURCE_WRITER_PHRASE


def test_detach_phrase_not_required_for_menu_approval() -> None:
    assert detach_execute_approved(True) is True
    assert detach_execute_approved("yes") is True
    assert detach_execute_approved(DETACH_CONFIRMATION) is True  # legacy CLI
    assert detach_execute_approved(False) is False
    assert detach_execute_approved("") is False
    assert detach_execute_approved("DETACH SOMETHING ELSE") is False


def test_writer_restore_exact_phrase_unchanged() -> None:
    assert RESTORE_SOURCE_WRITER_PHRASE == "RESTORE SOURCE WRITER"


def test_safe_disconnect_wizard_two_yes_no_only(monkeypatch) -> None:
    from mercury.storage import interactive_menu as menu

    prompts: list[str] = []
    asks: list[str] = []

    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **_k: SimpleNamespace(
            identity=SimpleNamespace(
                label="MERCURY_DATA_V2",
                uuid="715f29a9-2671-477b-8c8d-515d190addb9",
                model="WDC WD10JDRW-11CFYS0",
                partition_device="/dev/sdb1",
                parent_device="/dev/sdb",
                mountpoint="/mnt/MERCURY_DATA_V2",
            ),
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_presentation.print_safe_disconnect_intro",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "mercury.storage.detach_presentation.print_privileged_detach_prompt",
        lambda **_k: None,
    )
    monkeypatch.setattr(menu.output, "write", lambda *_a, **_k: None)
    monkeypatch.setattr(menu.display_screen, "write_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(menu.display_screen, "write_status", lambda *_a, **_k: None)

    def yes_no(prompt: str, default: bool = False) -> bool:
        prompts.append(prompt)
        # Cancel at stage 2
        if "Unmount and power off" in prompt:
            return False
        return "safe-disconnect checks" in prompt

    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", yes_no)
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda prompt, **_k: asks.append(prompt) or "",
    )

    preview = SimpleNamespace(
        ok=True,
        identity=SimpleNamespace(model="WDC", label="MERCURY_DATA_V2"),
        result_state="PREFLIGHT_OK",
        phases=[],
        package_id="pkg",
        blockers=[],
        user_messages=["Preflight passed; privileged holder checks and unmount not yet run."],
        safe_to_physically_disconnect=False,
    )
    execute_calls: list[dict] = []

    def fake_wizard(**kwargs):
        if kwargs.get("execute"):
            execute_calls.append(kwargs)
            raise AssertionError("execute must not run when stage-2 is declined")
        return preview

    monkeypatch.setattr("mercury.storage.detach_wizard.run_detach_wizard", fake_wizard)
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.format_wizard_report",
        lambda *_a, **_k: ["PREVIEW"],
    )

    assert menu._run_safe_disconnect_wizard_impl() is False
    assert len(prompts) == 2
    assert "safe-disconnect checks" in prompts[0]
    assert "Unmount and power off this Mercury HDD now?" in prompts[1]
    assert not any("DETACH MERCURY HDD" in p for p in prompts)
    assert not any("DETACH MERCURY HDD" in a for a in asks)
    assert execute_calls == []


def test_preflight_cancel_performs_no_privileged_action(monkeypatch) -> None:
    from mercury.storage import interactive_menu as menu

    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **_k: SimpleNamespace(identity=None, errors=[]),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_presentation.print_safe_disconnect_intro",
        lambda **_k: None,
    )
    monkeypatch.setattr(menu.display_screen, "write_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(menu.display_screen, "write_status", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_yes_no",
        lambda *_a, **_k: False,
    )
    called = {"wizard": 0}
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.run_detach_wizard",
        lambda **_k: called.__setitem__("wizard", called["wizard"] + 1),
    )
    assert menu._run_safe_disconnect_wizard_impl() is False
    assert called["wizard"] == 0


def test_successful_report_has_one_result_code_no_preview_leak() -> None:
    result = SimpleNamespace(
        result_state=HDD_POWERED_OFF_SAFE_TO_DISCONNECT,
        package_id="destination_rehearsal_final_source_20260723T205343Z",
        identity=SimpleNamespace(
            model="WDC WD10JDRW-11CFYS0",
            label="MERCURY_DATA_V2",
            uuid="715f29a9-2671-477b-8c8d-515d190addb9",
            partition_device="/dev/sdb1",
            parent_device="/dev/sdb",
        ),
        phases=[
            SimpleNamespace(
                name="device",
                ok=True,
                lines=["[PASS] device"],
                detail="",
            ),
            SimpleNamespace(
                name="holders",
                ok=True,
                lines=["[PASS] No system-wide open file handles"],
                detail="",
            ),
            SimpleNamespace(
                name="unmount",
                ok=True,
                lines=["[PASS] HDD unmounted", "[PASS] UUID no longer mounted"],
                detail="",
            ),
            SimpleNamespace(
                name="power_off",
                ok=True,
                lines=["[PASS] Mercury HDD powered off"],
                detail="",
            ),
        ],
        blockers=[],
        user_messages=[
            "Preflight passed; privileged holder checks and unmount not yet run.",
            "Preview only — no unmount or power-off performed.",
            "Result: SAFE TO DISCONNECT",
        ],
        safe_to_physically_disconnect=True,
    )
    # Simulate strip that execute path performs
    from mercury.storage.detach_wizard import _strip_preview_only_messages, DetachWizardResult

    real = DetachWizardResult(
        result_state=HDD_POWERED_OFF_SAFE_TO_DISCONNECT,
        ok=True,
        package_id=result.package_id,
        identity=result.identity,
        user_messages=list(result.user_messages),
    )
    _strip_preview_only_messages(real)
    assert not any("Preflight passed" in m for m in real.user_messages)
    assert not any("Preview only" in m for m in real.user_messages)

    privileged = "\n".join(format_privileged_detach_report(result))
    assert "[PASS] device" not in privileged
    assert "[PASS] No system-wide open file handles" in privileged
    assert "[PASS] Mercury HDD powered off" in privileged

    complete = "\n".join(format_disconnect_complete(result))
    assert complete.count("Result:") == 1
    assert "HDD_POWERED_OFF_SAFE_TO_DISCONNECT" in complete
    assert "Result: SAFE TO DISCONNECT" not in complete
    assert "MERCURY_DATA_USB" in complete
    assert "SAFE DISCONNECT COMPLETE" in complete


def test_intentional_detach_differs_from_unexpected_absence(tmp_path: Path) -> None:
    intentional = HostMaintenanceState(
        storage_availability="detached",
        writes_allowed=False,
        active_write_role="none",
        package_id="pkg",
        package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        destination_rehearsal_active=True,
        intentional_safe_disconnect=True,
        last_safe_disconnect_result=HDD_POWERED_OFF_SAFE_TO_DISCONNECT,
        notes="Mercury HDD detached for destination rehearsal",
    )
    unexpected = HostMaintenanceState(
        storage_availability="detached",
        writes_allowed=False,
        active_write_role="none",
        package_id="",
        package_verification_status="",
        destination_rehearsal_active=False,
        intentional_safe_disconnect=False,
        notes="",
    )
    assert intentional_safe_disconnect_active(intentional) is True
    assert intentional_safe_disconnect_active(unexpected) is False

    from mercury.menu.recommendation import build_main_menu_recommendation

    host_path = tmp_path / "host.json"
    save_host_maintenance(intentional, path=host_path)
    import os

    os.environ["MERCURY_HOST_MAINTENANCE_PATH"] = str(host_path)
    rec = build_main_menu_recommendation(host=intentional)
    assert rec.recommended_action == "physical_move"
    assert "destination" in rec.explanation.lower()
    assert rec.recommended_label == "Move HDD to destination workstation"

    from mercury.menu.recommendation import main_menu_action_for_recommendation

    assert main_menu_action_for_recommendation("physical_move") is None

    rec2 = build_main_menu_recommendation(host=unexpected)
    assert rec2.recommended_action == "attach"
    assert "Reconnect" in rec2.explanation


def test_mount_hint_suppressed_after_intentional_detach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_path = tmp_path / "host.json"
    state = HostMaintenanceState(
        storage_availability="detached",
        writes_allowed=False,
        active_write_role="none",
        package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        destination_rehearsal_active=True,
        intentional_safe_disconnect=True,
        notes="Mercury HDD detached for destination rehearsal",
    )
    save_host_maintenance(state, path=host_path)
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(host_path))
    monkeypatch.setattr(
        "mercury.repair.startup._hdd_writer_active",
        lambda: True,
    )
    from mercury.repair.startup import primary_mount_hint

    assert primary_mount_hint() is None


def test_physical_move_ready_screen_after_success(monkeypatch) -> None:
    from mercury.storage import interactive_menu as menu
    from mercury.storage.detach_wizard import DetachWizardResult, HDD_POWERED_OFF_SAFE_TO_DISCONNECT

    identity = SimpleNamespace(
        model="WDC WD10JDRW-11CFYS0",
        label="MERCURY_DATA_V2",
        uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        partition_device="/dev/sdb1",
        parent_device="/dev/sdb",
        mountpoint="/mnt/MERCURY_DATA_V2",
    )
    preview = DetachWizardResult(
        result_state="PREFLIGHT_OK",
        ok=True,
        identity=identity,
        package_id="pkg_test",
        user_messages=["Preflight passed; privileged holder checks and unmount not yet run."],
    )
    success = DetachWizardResult(
        result_state=HDD_POWERED_OFF_SAFE_TO_DISCONNECT,
        ok=True,
        identity=identity,
        package_id="pkg_test",
        phases=[],
        user_messages=[],
    )

    answers_yes = iter([True, True])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_yes_no",
        lambda *_a, **_k: next(answers_yes),
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda *_a, **_k: "0",
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **_k: SimpleNamespace(identity=identity, errors=[]),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_presentation.print_safe_disconnect_intro",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "mercury.storage.detach_presentation.print_privileged_detach_prompt",
        lambda **_k: None,
    )
    captured: list[str] = []
    monkeypatch.setattr(menu.output, "write", lambda s: captured.append(str(s)))
    monkeypatch.setattr(menu.display_screen, "write_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(menu.display_screen, "write_status", lambda *_a, **_k: None)

    def fake_wizard(**kwargs):
        return success if kwargs.get("execute") else preview

    monkeypatch.setattr("mercury.storage.detach_wizard.run_detach_wizard", fake_wizard)

    outcome = menu._run_safe_disconnect_wizard_impl()
    assert outcome == "exit"
    joined = "\n".join(captured)
    assert "PHYSICAL MOVE READY" in joined or "Move the HDD" in joined
    # Preview may mention preflight once; it must not leak after privileged work.
    marker = "PRIVILEGED DETACH"
    if marker not in joined:
        marker = "SAFE DISCONNECT COMPLETE"
    tail = joined[joined.find(marker) :] if marker in joined else joined
    assert "Preflight passed; privileged holder checks and unmount not yet run" not in tail
    assert "Preview only — no unmount or power-off performed" not in tail
    assert joined.count("Result: SAFE TO DISCONNECT") == 0
    assert "DETACH MERCURY HDD" not in joined

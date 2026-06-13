"""Tests for interactive USB repair at menu startup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.core.environment_status import EnvironmentStatus
from mercury.core.path_permissions import PathPermissionCheck
from mercury.core.usb_mount import resolve_usb_mount
from mercury.repair.startup import (
    apply_usb_repair,
    maybe_prompt_usb_repair_at_startup,
    run_usb_repair_flow,
    usb_repair_reason,
    usb_repair_session_skipped,
)


def _env(*, repair_banner: str | None = None, permission_checks: tuple = ()) -> EnvironmentStatus:
    mount = Path("/mnt/MERCURY_DATA_USB")
    return SimpleNamespace(
        usb=SimpleNamespace(
            mount_path=mount,
            repair_banner=repair_banner,
        ),
        permission_checks=permission_checks,
    )


def test_usb_repair_reason_when_unmounted() -> None:
    reason = usb_repair_reason(
        _env(repair_banner="Mercury USB is not ready at /mnt/MERCURY_DATA_USB.")
    )
    assert reason is not None
    assert "not mounted" in reason.lower()


def test_usb_repair_reason_for_root_owned_usb_log() -> None:
    mount = Path("/mnt/MERCURY_DATA_USB")
    reason = usb_repair_reason(
        _env(
            permission_checks=(
                PathPermissionCheck(
                    path=mount / "mercury_logs",
                    label="USB log directory",
                    exists=True,
                    writable=False,
                    owner="root",
                    owner_mismatch=True,
                    detail="not writable (owner: root)",
                ),
            )
        )
    )
    assert reason is not None
    assert "not writable" in reason


def test_usb_repair_reason_ignores_non_usb_paths() -> None:
    reason = usb_repair_reason(
        _env(
            permission_checks=(
                PathPermissionCheck(
                    path=Path("/tmp/mercury_logs"),
                    label="configured log directory",
                    exists=True,
                    writable=False,
                    owner="root",
                    owner_mismatch=True,
                    detail="not writable (owner: root)",
                ),
            )
        )
    )
    assert reason is None


def test_maybe_prompt_skips_when_usb_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mercury.repair.startup.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "mercury.repair.startup.build_environment_status",
        lambda **kwargs: _env(),
    )
    called = {"ask": False}

    def _ask(*args, **kwargs):
        called["ask"] = True
        return True

    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", _ask)
    maybe_prompt_usb_repair_at_startup()
    assert called["ask"] is False


def test_maybe_prompt_runs_repair_when_user_confirms(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mount = Path("/mnt/MERCURY_DATA_USB")
    states = iter(
        [
            _env(repair_banner="Mercury USB is not ready."),
            _env(),
        ]
    )

    monkeypatch.setattr("mercury.repair.startup.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "mercury.repair.startup.build_environment_status",
        lambda **kwargs: next(states),
    )
    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", lambda *args, **kwargs: True)
    monkeypatch.setattr("mercury.repair.startup.apply_usb_repair", lambda: True)
    monkeypatch.setattr("mercury.repair.startup._reconfigure_logging_after_repair", lambda: None)

    maybe_prompt_usb_repair_at_startup()
    out = capsys.readouterr().out
    assert "Run USB repair now?" not in out
    assert "USB repair completed" in out


def test_maybe_prompt_skips_when_user_declines(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("MERCURY_SKIP_USB_REPAIR", raising=False)
    monkeypatch.setattr("mercury.repair.startup.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "mercury.repair.startup.build_environment_status",
        lambda **kwargs: _env(repair_banner="Mercury USB is not ready."),
    )
    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", lambda *args, **kwargs: False)
    called = {"repair": False}
    monkeypatch.setattr(
        "mercury.repair.startup.apply_usb_repair",
        lambda: called.__setitem__("repair", True) or True,
    )

    assert run_usb_repair_flow(interactive=True) is False
    out = capsys.readouterr().out
    assert "skipped" in out.lower()
    assert called["repair"] is False
    assert usb_repair_session_skipped() is True


def test_main_menu_usb_repair_hint_when_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.repair.startup import main_menu_usb_repair_hint

    monkeypatch.delenv("MERCURY_SKIP_USB_REPAIR", raising=False)
    monkeypatch.setattr(
        "mercury.repair.startup.usb_repair_needed",
        lambda **kwargs: True,
    )
    hint = main_menu_usb_repair_hint()
    assert hint is not None
    assert "enter r" in hint


def test_main_menu_usb_repair_hint_hidden_after_decline(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.repair.startup import main_menu_usb_repair_hint, skip_usb_repair_for_session

    skip_usb_repair_for_session()
    assert main_menu_usb_repair_hint() is None


def test_run_menu_accepts_r_to_repair_usb(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.menu.runners import run_menu

    inputs = iter(["r", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda _prompt="": next(inputs))
    monkeypatch.setattr("mercury.repair.startup.maybe_prompt_usb_repair_at_startup", lambda: None)
    called = {"repair": False}

    def _repair(**kwargs):
        called["repair"] = True
        return True

    monkeypatch.setattr("mercury.repair.startup.run_usb_repair_flow", _repair)
    run_menu(interactive=True)
    assert called["repair"] is True


def test_apply_usb_repair_invokes_sudo_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = tmp_path / "repair-mercury-usb.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    captured: list[list[str]] = []

    def _run(argv, check=False):
        captured.append(list(argv))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("mercury.repair.startup.usb_repair_script_path", lambda: script)
    monkeypatch.setattr("mercury.repair.startup.subprocess.run", _run)
    monkeypatch.setattr("mercury.repair.startup.os.geteuid", lambda: 1000)

    assert apply_usb_repair() is True
    assert captured == [["sudo", str(script)]]

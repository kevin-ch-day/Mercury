"""Restore hub now opens the consolidated recovery dashboard."""

from __future__ import annotations

import pytest

from mercury.menu.options import MAIN_RECOVERY


def test_recovery_hub_opens_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.run_recovery_dashboard",
        lambda **_k: called.append("dash"),
    )
    from mercury.menu.task_menus import run_recovery_hub

    run_recovery_hub()
    assert called == ["dash"]


def test_main_five_still_recovery_hub() -> None:
    from mercury.menu.actions import menu_actions

    assert menu_actions()["5"].action_id == MAIN_RECOVERY

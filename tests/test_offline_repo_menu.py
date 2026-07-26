"""Interactive confirmation contract for offline repository copies."""

from __future__ import annotations

from mercury.menu import prompts
from mercury.repo import interactive_menu


def test_offline_repo_sync_uses_a_yes_no_confirmation(monkeypatch) -> None:
    prompts_seen: list[tuple[str, bool | None]] = []

    class _Plan:
        root = "/mnt/MERCURY_DATA_V2/mercury_repo_clones"

    monkeypatch.setattr(interactive_menu, "offline_clone_plan", lambda: _Plan())
    monkeypatch.setattr(
        interactive_menu,
        "print_offline_clone_plan",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        interactive_menu,
        "execute_offline_clone_plan",
        lambda plan: plan,
    )
    monkeypatch.setattr(
        prompts,
        "ask_yes_no",
        lambda prompt, *, default=None: prompts_seen.append((prompt, default)) or False,
    )

    interactive_menu.run_offline_sync_now()

    assert prompts_seen == [("Sync offline HDD repository copies now?", False)]


def test_offline_repo_menu_opens_unified_git_hub(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.task_menus.run_repo_hub",
        lambda **_k: called.append("hub"),
    )
    interactive_menu.run_offline_repo_menu()
    assert called == ["hub"]

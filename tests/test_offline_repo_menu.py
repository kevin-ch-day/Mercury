"""Interactive confirmation contract for offline repository copies."""

from __future__ import annotations

from mercury.menu import prompts
from mercury.repo import interactive_menu


def test_offline_repo_sync_uses_a_yes_no_confirmation(monkeypatch) -> None:
    prompts_seen: list[tuple[str, bool | None]] = []
    inputs = iter(["1", "0"])

    class _Plan:
        root = "/mnt/MERCURY_DATA_V2/mercury_repo_clones"

    monkeypatch.setattr(interactive_menu, "_plan", lambda: _Plan())
    monkeypatch.setattr(interactive_menu, "print_offline_clone_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interactive_menu, "render_submenu", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interactive_menu, "read_submenu_choice", lambda: next(inputs))
    monkeypatch.setattr(
        prompts,
        "ask_yes_no",
        lambda prompt, *, default=None: prompts_seen.append((prompt, default)) or False,
    )

    interactive_menu.run_offline_repo_menu()

    assert prompts_seen == [("Sync offline HDD repository copies now?", False)]

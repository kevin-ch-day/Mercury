"""Legacy Advanced-routing cleanup regressions (superseded by nine-area console)."""

from __future__ import annotations

from mercury.menu.options import MAIN_ADVANCED, main_menu_hint, main_menu_items, main_menu_max_primary_actions


def test_advanced_main_menu_entry_gone() -> None:
    assert main_menu_max_primary_actions() == 9
    titles = " ".join(t for _k, t in main_menu_items(writes_allowed=True))
    assert "Advanced tools" not in titles
    assert main_menu_hint(MAIN_ADVANCED).endswith("[1]")


def test_software_only_has_no_advanced_slot() -> None:
    titles = " ".join(t for _k, t in main_menu_items(software_only=True))
    assert "Advanced" not in titles
    assert main_menu_max_primary_actions(software_only=True) == 5

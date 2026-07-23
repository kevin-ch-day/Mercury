"""Menu-reference contracts for operator-facing cross-navigation text."""

from __future__ import annotations

from mercury.handoff.display import handoff_dashboard_line
from mercury.menu.main_display import render_menu_help
from mercury.menu.options import ACTION_HANDOFF, main_menu_hint


def test_handoff_navigation_uses_symbolic_main_menu_hint() -> None:
    handoff = main_menu_hint(ACTION_HANDOFF)
    assert handoff in handoff_dashboard_line(verified_count=4, source_count=4)
    assert handoff in handoff_dashboard_line(verified_count=0, source_count=4, missing_count=1)
    assert handoff in handoff_dashboard_line(verified_count=3, source_count=4, absent_count=1)
    assert handoff in render_menu_help()
    assert "[10] handoff" not in handoff_dashboard_line(verified_count=4, source_count=4)

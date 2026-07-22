"""Menu-reference contracts for operator-facing cross-navigation text."""

from __future__ import annotations


def test_handoff_navigation_uses_current_main_menu_option() -> None:
    from mercury.handoff.display import handoff_dashboard_line
    from mercury.menu.main_display import render_menu_help

    assert "[10] handoff" in handoff_dashboard_line(verified_count=4, source_count=4)
    assert "[10] checklist" in handoff_dashboard_line(verified_count=0, source_count=4, missing_count=1)
    assert "[10] checklist" in handoff_dashboard_line(verified_count=3, source_count=4, absent_count=1)
    assert "Handoff: menu 10" in render_menu_help()

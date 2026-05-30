"""Shared terminal formatting for Mercury CLI and menu output."""

from mercury.terminal.format import (
    format_bytes,
    format_count_summary,
    format_dashboard_row,
    format_menu_rule,
    format_menu_status_row,
    format_pair,
    format_plan_status,
    format_report_header,
    format_table,
    format_yes_no,
    short_path,
)
from mercury.terminal.table import TableStyle, format_table as format_table_rows
from mercury.terminal.theme import (
    body_label,
    colors_enabled,
    dashboard_row,
    get_theme,
    menu_bottom_option,
    menu_item_line,
    menu_status_row,
    menu_subtitle_line,
    menu_title_line,
    rule_line,
    set_color_enabled,
    strip_markup,
    tag,
    tag_plain,
)

__all__ = [
    "TableStyle",
    "body_label",
    "colors_enabled",
    "dashboard_row",
    "format_bytes",
    "format_count_summary",
    "format_dashboard_row",
    "format_menu_rule",
    "format_menu_status_row",
    "format_pair",
    "format_plan_status",
    "format_report_header",
    "format_table",
    "format_table_rows",
    "format_yes_no",
    "get_theme",
    "menu_bottom_option",
    "menu_item_line",
    "menu_status_row",
    "menu_subtitle_line",
    "menu_title_line",
    "open_screen",
    "rule_line",
    "set_color_enabled",
    "short_path",
    "strip_markup",
    "tag",
    "tag_plain",
    "write_blank",
    "write_bullets",
    "write_count_header",
    "write_fields",
    "write_footer_note",
    "write_hint",
    "write_list",
    "write_report_header",
    "write_section",
    "write_status",
    "write_summary",
    "write_table",
]

_SCREEN_EXPORTS = frozenset(
    {
        "open_screen",
        "write_blank",
        "write_bullets",
        "write_count_header",
        "write_fields",
        "write_footer_note",
        "write_hint",
        "write_list",
        "write_report_header",
        "write_section",
        "write_status",
        "write_summary",
        "write_table",
    }
)


def __getattr__(name: str):
    if name in _SCREEN_EXPORTS:
        from mercury.terminal import screen as screen_module

        return getattr(screen_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

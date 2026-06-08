"""Tests for shared menu display helpers."""

from mercury import menu_display


def test_status_line_includes_tags() -> None:
    line = menu_display.status_line(probe_database=False)
    assert "Status:" in line
    assert "[--]" in line or "[ok]" in line


def test_dashboard_rows_show_connection_and_backups() -> None:
    rows = menu_display.dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "MariaDB" in text
    assert "Backup target" in text


def test_status_rows_include_operator_fields() -> None:
    rows = menu_display.status_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Mode" in text
    assert "Database" in text
    assert "Backups" in text


def test_format_menu_bottom_option_exit_and_return() -> None:
    assert menu_display.format_menu_bottom_option("Exit") == "      [0] Exit"
    assert menu_display.format_menu_bottom_option("Return") == "      [0] Return"


def test_render_option_menu_puts_return_last() -> None:
    text = menu_display.render_option_menu(
        title="Sub menu",
        options=[("1", "First"), ("2", "Second")],
        bottom_label="Return",
    )
    lines = text.splitlines()
    assert lines[-1] == "      [0] Return"
    assert "      [1] First" in lines


def test_render_main_menu_matches_simple_layout() -> None:
    text = menu_display.render_main_menu()
    assert menu_display.MENU_TITLE in text
    assert menu_display.MENU_SUBTITLE in text
    assert "Status" in text
    assert "Backup target" in text
    assert "Source DBs" in text
    assert "Execution Safety" not in text
    assert "─" in text
    assert "      [1] Environment check" in text
    assert "      [0] Exit" in text
    assert "Actions" in text


def test_render_main_menu_body_omits_title_block() -> None:
    body = menu_display.render_main_menu_body()
    assert menu_display.MENU_TITLE not in body
    assert "Status" in body
    assert "MariaDB" in body
    assert "      [1] Environment check" in body


def test_render_menu_help_lists_shortcuts() -> None:
    help_text = menu_display.render_menu_help()
    assert "Operator console help" in help_text
    assert "0 or q to exit" in help_text

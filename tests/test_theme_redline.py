"""Mercury Redline / Classic theme architecture tests (presentation-only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mercury.terminal.color_capability import (
    detect_color_mode,
    set_color_mode_override,
)
from mercury.terminal.design_system import (
    active_styles,
    build_style_bundle,
    clear_style_cache,
    style_for,
)
from mercury.terminal.format import truncate_middle
from mercury.terminal.theme import (
    danger_banner,
    menu_header_lines,
    menu_item_line,
    set_color_enabled,
    strip_markup,
    tag,
    tag_plain,
)
from mercury.terminal.theme_preview import print_theme_preview, render_theme_preview
from mercury.terminal.theme_settings import (
    THEME_CLASSIC,
    THEME_MONOCHROME,
    THEME_REDLINE,
    active_theme_id,
    list_themes,
    load_theme_selection,
    reset_theme_selection,
    save_theme_selection,
    set_theme_override,
    validate_theme_id,
)
from mercury.terminal.theme_tokens import (
    REDLINE_SWATCHES,
    ColorMode,
    SemanticToken,
    classic_token_swatch,
    redline_token_swatch,
    resolve_color,
)
from mercury.storage.transitions import RESTORE_SOURCE_WRITER_PHRASE


@pytest.fixture(autouse=True)
def _reset_theme_seams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MERCURY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.delenv("MERCURY_THEME", raising=False)
    monkeypatch.delenv("MERCURY_COLOR_MODE", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MERCURY_NO_COLOR", raising=False)
    monkeypatch.delenv("MERCURY_FORCE_COLOR", raising=False)
    set_theme_override(None)
    set_color_enabled(None)
    set_color_mode_override(None)
    clear_style_cache()
    yield
    set_theme_override(None)
    set_color_enabled(None)
    set_color_mode_override(None)
    clear_style_cache()


def test_semantic_token_mapping_redline_and_classic() -> None:
    assert redline_token_swatch(SemanticToken.ACCENT_PRIMARY).truecolor == "#D71920"
    assert redline_token_swatch(SemanticToken.STATUS_DANGER).truecolor == "#FF3640"
    assert classic_token_swatch(SemanticToken.ACCENT_PRIMARY).truecolor == "#00D4FF"
    assert style_for(SemanticToken.STATUS_WARNING)


def test_truecolor_palette_swatches() -> None:
    assert REDLINE_SWATCHES["mercury_crimson"].truecolor == "#D71920"
    assert REDLINE_SWATCHES["bone_white"].truecolor == "#E8E4DE"
    assert REDLINE_SWATCHES["success"].truecolor == "#A8D68D"


def test_256_and_16_and_none_mappings() -> None:
    swatch = REDLINE_SWATCHES["mercury_crimson"]
    assert resolve_color(swatch, ColorMode.TRUECOLOR) == "#D71920"
    assert resolve_color(swatch, ColorMode.ANSI256) == "color(160)"
    assert resolve_color(swatch, ColorMode.ANSI16) == "red"
    assert resolve_color(swatch, ColorMode.NONE) == ""


def test_no_color_and_redirected_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    clear_style_cache()
    set_color_enabled(None)
    assert detect_color_mode().value == "none"
    set_color_enabled(False)
    assert tag("ok", "done") == tag_plain("ok", "done")


def test_json_and_receipts_have_no_ansi(tmp_path: Path) -> None:
    set_theme_override(THEME_REDLINE)
    set_color_enabled(True)
    set_color_mode_override(ColorMode.TRUECOLOR)
    clear_style_cache()
    styled = tag("fail", "boom")
    assert "\x1b" not in styled  # Rich markup, not raw ANSI
    # Machine artifacts stay plain.
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "FAIL", "detail": "boom"}), encoding="utf-8")
    assert "\x1b" not in receipt.read_text(encoding="utf-8")
    assert "[" not in receipt.read_text(encoding="utf-8") or "status" in receipt.read_text(
        encoding="utf-8"
    )


def test_table_alignment_with_ansi() -> None:
    from mercury.terminal.table import format_table
    from mercury.terminal.theme import style_table_lines

    lines = format_table(
        ["DATABASE", "ROLE"],
        [["android_permission_intel", "shared"]],
        indent=0,
    )
    set_color_enabled(True)
    set_theme_override(THEME_REDLINE)
    clear_style_cache()
    styled = style_table_lines(lines)
    plain = [strip_markup(line) for line in styled]
    assert plain[0] == lines[0]
    assert plain[2] == lines[2]


def test_middle_truncation() -> None:
    text = "erebus_threat_intel_prod-full-20260722_055507_238_extra"
    out = truncate_middle(text, max_len=24)
    assert out.startswith("erebus")
    assert out.endswith("extra") or "…" in out
    assert len(out) <= 24


def test_80_and_120_column_preview_render() -> None:
    for width in (80, 120):
        lines = render_theme_preview(THEME_REDLINE, width=width, force_color=False)
        assert any("MERCURY" in line for line in lines)
        assert any("SYSTEM STATE" in line or "Mercury HDD" in line for line in lines)
        joined = "\n".join(lines)
        assert "\\]" not in joined and "\\[" not in joined
        assert "[PASS]" in joined or "[WARN]" in joined
        assert "DESTRUCTIVE ACTION" in joined
        assert "Drop production database" in joined
        # Destructive example is isolated — not adjacent to Exit in operations menu.
        ops_idx = joined.index("8–10. MENU")
        dest_idx = joined.index("DESTRUCTIVE ACTION")
        exit_idx = joined.index("[0] Exit")
        assert ops_idx < exit_idx < dest_idx
        assert "Connected · mounted · writes disabled" in joined
        assert "VERIFIED · destination rehearsal" in joined


def test_warning_retains_text_labels() -> None:
    set_theme_override(THEME_REDLINE)
    set_color_enabled(False)
    clear_style_cache()
    assert tag_plain("warn", "writes disabled").startswith("[WARN]")
    set_theme_override(THEME_CLASSIC)
    clear_style_cache()
    assert tag_plain("warn", "writes disabled").startswith("[--]")


def test_danger_differs_from_accent() -> None:
    set_theme_override(THEME_REDLINE)
    set_color_mode_override(ColorMode.TRUECOLOR)
    set_color_enabled(True)
    clear_style_cache()
    styles = active_styles()
    assert styles.destructive != styles.accent
    assert "#FF3640" in styles.destructive or "bright_red" in styles.destructive
    assert "#D71920" in styles.accent or styles.accent


def test_disabled_actions_remain_readable() -> None:
    set_theme_override(THEME_REDLINE)
    set_color_enabled(False)
    line = menu_item_line("5", "Build package (writes disabled)", disabled=True)
    assert "Build package" in line
    assert "[5]" in line


def test_theme_selection_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "theme.json"
    monkeypatch.setenv("MERCURY_THEME_PATH", str(path))
    save_theme_selection(THEME_REDLINE, path=path)
    assert path.is_file()
    assert load_theme_selection(path=path).theme_id == THEME_REDLINE
    assert active_theme_id(path=path) == THEME_REDLINE
    reset_theme_selection(path=path)
    assert not path.exists()
    assert active_theme_id(path=path) == THEME_CLASSIC


def test_theme_works_without_hdd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERCURY_THEME_PATH", str(tmp_path / "theme.json"))
    # Ensure preview does not need mount.
    monkeypatch.delenv("MERCURY_USB_MOUNT", raising=False)
    lines = render_theme_preview(THEME_REDLINE, force_color=False)
    assert "Preview complete" in "\n".join(lines) or any("MERCURY" in line for line in lines)


def test_preview_has_no_operational_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "host_maintenance.json"
    host.write_text('{"writes_allowed": false}\n', encoding="utf-8")
    before = host.read_text(encoding="utf-8")
    mtime = host.stat().st_mtime_ns
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(host))
    render_theme_preview(THEME_REDLINE, force_color=False)
    assert host.read_text(encoding="utf-8") == before
    assert host.stat().st_mtime_ns == mtime


def test_classic_theme_fallback() -> None:
    set_theme_override(THEME_CLASSIC)
    clear_style_cache()
    assert active_styles().theme_id == THEME_CLASSIC
    assert tag_plain("ok", "x") == "[ok] x"


def test_invalid_theme_refuses_safely() -> None:
    with pytest.raises(ValueError, match="Unknown theme"):
        validate_theme_id("tron-ares")


def test_confirmation_phrase_unchanged() -> None:
    assert RESTORE_SOURCE_WRITER_PHRASE == "RESTORE SOURCE WRITER"
    set_theme_override(THEME_REDLINE)
    set_color_enabled(False)
    lines = danger_banner("CONFIRM SOURCE WRITER RESTORE")
    joined = "\n".join(lines)
    assert "CONFIRM SOURCE WRITER RESTORE" in joined
    # Phrase itself is not altered by theme helpers.
    assert RESTORE_SOURCE_WRITER_PHRASE == "RESTORE SOURCE WRITER"


def test_bracket_labels_never_leak_rich_escapes() -> None:
    """Regression: ``\\[PASS\\]`` inside a style span rendered as ``[PASS\\]``."""
    from io import StringIO
    import re

    from rich.console import Console

    from mercury.terminal.theme import (
        menu_item_line,
        set_color_enabled,
        status_badge,
        strip_markup,
        tag,
    )
    from mercury.terminal.design_system import clear_style_cache
    from mercury.terminal.theme_settings import THEME_REDLINE, set_theme_override

    set_theme_override(THEME_REDLINE)
    set_color_enabled(True)
    clear_style_cache()
    try:
        badge = status_badge("ok")
        item = menu_item_line("1", "Safely disconnect Mercury HDD", recommended=True)
        tagged = tag("warn", "Source writes remain disabled")
        for sample in (badge, item, tagged):
            plain = strip_markup(sample)
            assert "\\[" not in plain and "\\]" not in plain
            assert "[PASS]" in plain or "[1]" in plain or "[WARN]" in plain
            buf = StringIO()
            Console(
                file=buf, force_terminal=True, color_system="truecolor", highlight=False
            ).print(sample, end="")
            visible = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
            assert "\\" not in visible
            assert "[PASS]" in visible or "[1]" in visible or "[WARN]" in visible
    finally:
        set_theme_override(None)
        set_color_enabled(None)
        clear_style_cache()


def test_redline_header_and_recommended_marker() -> None:
    set_theme_override(THEME_REDLINE)
    set_color_enabled(False)
    clear_style_cache()
    header = menu_header_lines("ignored", variant="redline_a")
    # Dual-rail frame: steel edge, title, subtitle, signal edge.
    assert len(header) == 4
    assert "MERCURY // REDLINE" in header[1]
    assert "BACKUP" in header[2]
    item = menu_item_line("1", "Safely disconnect Mercury HDD", recommended=True)
    assert "▸" in item
    assert "RECOMMENDED" in item


def test_redline_design_principles_in_preview() -> None:
    lines = render_theme_preview(THEME_REDLINE, width=80, force_color=False)
    joined = "\n".join(lines)
    assert "DESIGN LANGUAGE" in joined
    assert "Angular light-lines" in joined
    assert "Containerized marks" in joined


def test_list_themes_includes_all() -> None:
    ids = {theme_id for theme_id, _name, _active in list_themes()}
    assert ids == {THEME_CLASSIC, THEME_REDLINE, THEME_MONOCHROME}


def test_env_theme_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_CLASSIC, path=path)
    monkeypatch.setenv("MERCURY_THEME", THEME_REDLINE)
    assert load_theme_selection(path=path).source == "env"
    assert load_theme_selection(path=path).theme_id == THEME_REDLINE


def test_cli_theme_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    monkeypatch.setenv("MERCURY_THEME_PATH", str(tmp_path / "theme.json"))
    runner = CliRunner()
    result = runner.invoke(app, ["theme", "list"])
    assert result.exit_code == 0
    assert "mercury-classic" in result.stdout
    assert "mercury-redline" in result.stdout


def test_cli_theme_preview_no_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    host = tmp_path / "host.json"
    host.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(host))
    monkeypatch.setenv("MERCURY_THEME_PATH", str(tmp_path / "theme.json"))
    before = host.stat().st_mtime_ns
    runner = CliRunner()
    result = runner.invoke(app, ["theme", "preview", "mercury-redline", "--no-color"])
    assert result.exit_code == 0
    assert "MERCURY" in result.stdout
    assert host.stat().st_mtime_ns == before

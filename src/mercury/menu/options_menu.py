"""Host-local Options / Appearance menus (no Mercury HDD required)."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.terminal.color_capability import detect_color_mode, requested_color_mode
from mercury.terminal.theme import (
    colors_enabled,
    dashboard_row,
    menu_item_line,
    rule_line,
    section_title,
)
from mercury.terminal.theme_preview import print_theme_preview
from mercury.terminal.theme_settings import (
    COLOR_MODE_CHOICES,
    THEME_CLASSIC,
    THEME_DISPLAY_NAMES,
    THEME_REDLINE,
    active_theme_id,
    default_theme_path,
    load_theme_selection,
    reload_appearance,
    reset_theme_selection,
    save_color_mode,
    save_theme_selection,
)
from mercury.terminal.theme_tokens import ColorMode

# Symbolic Options actions (numbers assigned at render time).
OPTIONS_APPEARANCE = "appearance"
OPTIONS_DISPLAY = "display"
OPTIONS_RESET = "reset"
APPEARANCE_USE_REDLINE = "use_redline"
APPEARANCE_USE_CLASSIC = "use_classic"
APPEARANCE_PREVIEW = "preview"
APPEARANCE_COLOR = "color"
APPEARANCE_RESET = "reset"


def _preference_source_label(source: str) -> str:
    return {
        "env": "Environment (MERCURY_THEME)",
        "file": "Host-local",
        "default": "Compiled default",
        "override": "Session override",
    }.get(source, source)


def _color_mode_label(mode_id: str) -> str:
    for key, label in COLOR_MODE_CHOICES:
        if key == mode_id:
            return label
    return mode_id


def _detected_color_label() -> str:
    detected = detect_color_mode()
    mapping = {
        ColorMode.TRUECOLOR: "truecolor",
        ColorMode.ANSI256: "256 color",
        ColorMode.ANSI16: "16 color",
        ColorMode.NONE: "none",
        ColorMode.AUTO: "auto",
    }
    return mapping.get(detected, detected.value)


def _print_screen_header(title: str) -> None:
    if colors_enabled():
        output.write(section_title(title))
    else:
        output.write(title)
    output.write(rule_line(level="normal"))


def _print_status_block(rows: list[tuple[str, str]]) -> None:
    for label, value in rows:
        output.write(dashboard_row(label, value, label_width=14))


def _write_action_menu(actions: list[tuple[str, str]]) -> None:
    for index, (_aid, label) in enumerate(actions, start=1):
        output.write(menu_item_line(str(index), label, indent=2))
    output.write(menu_item_line("0", "Back", indent=2))
    output.write("")


def _read_action_choice(actions: list[tuple[str, str]]) -> str | None:
    choice = (menu_prompts.ask("Choice") or "").strip()
    if choice in {"", "0"}:
        return None
    for index, (aid, _label) in enumerate(actions, start=1):
        if choice == str(index):
            return aid
    output.write(menu_prompts.invalid_choice_message(choice))
    return ""


def _print_theme_updated(theme_id: str, *, path, active: str) -> None:
    if active != theme_id:
        _print_screen_header("Theme preference saved")
        _print_status_block(
            [
                ("Saved theme", THEME_DISPLAY_NAMES.get(theme_id, theme_id)),
                (
                    "Active now",
                    THEME_DISPLAY_NAMES.get(active, active),
                ),
                ("Blocked by", "MERCURY_THEME environment override"),
                ("Stored at", str(path)),
                ("Mercury HDD", "Unchanged"),
            ]
        )
    else:
        _print_screen_header("Theme updated")
        _print_status_block(
            [
                ("Active theme", THEME_DISPLAY_NAMES.get(theme_id, theme_id)),
                ("Stored locally", "Yes"),
                ("Mercury HDD", "Unchanged"),
            ]
        )
    output.write("")


def run_options_menu() -> None:
    """Top-level Options hub (startup shortcut and shared entrypoint)."""
    while True:
        selection = load_theme_selection()
        display_screen.open_screen("OPTIONS")
        pref = _preference_source_label(selection.source)
        color_line = (
            f"{_color_mode_label(selection.color_mode)} · {_detected_color_label()} detected"
            if selection.color_mode == "auto"
            else _color_mode_label(selection.color_mode)
        )
        if not colors_enabled():
            color_line = f"{color_line} (NO_COLOR wins)"
        _print_status_block(
            [
                (
                    "Theme",
                    THEME_DISPLAY_NAMES.get(selection.theme_id, selection.theme_id),
                ),
                ("Color mode", color_line),
                ("Preference", pref),
                ("Mercury HDD", "Not affected"),
            ]
        )
        output.write("")
        actions = [
            (OPTIONS_APPEARANCE, "Appearance and theme"),
            (OPTIONS_DISPLAY, "Display preferences"),
            (OPTIONS_RESET, "Reset options"),
        ]
        _write_action_menu(actions)
        selected = _read_action_choice(actions)
        if selected is None:
            return
        if selected == "":
            continue
        if selected == OPTIONS_APPEARANCE:
            run_appearance_menu()
            continue
        if selected == OPTIONS_DISPLAY:
            run_display_preferences_menu()
            continue
        if selected == OPTIONS_RESET:
            run_reset_options()
            continue


def run_display_preferences_menu() -> None:
    """Narrow display settings hub (currently color mode only)."""
    while True:
        selection = load_theme_selection()
        display_screen.open_screen("DISPLAY PREFERENCES")
        color_line = (
            f"{_color_mode_label(selection.color_mode)} · {_detected_color_label()} detected"
            if selection.color_mode == "auto"
            else _color_mode_label(selection.color_mode)
        )
        _print_status_block(
            [
                ("Color mode", color_line),
                ("Scope", "Interactive TTY only"),
                ("Mercury HDD", "Not affected"),
            ]
        )
        output.write("")
        actions = [("color", "Color mode")]
        _write_action_menu(actions)
        selected = _read_action_choice(actions)
        if selected is None:
            return
        if selected == "":
            continue
        if selected == "color":
            run_color_mode_menu()
            continue


def run_appearance_menu() -> None:
    """Host-local theme selection (no Mercury HDD required)."""
    while True:
        selection = load_theme_selection()
        active = selection.theme_id
        display_screen.open_screen("APPEARANCE AND THEME")
        available = " · ".join(
            THEME_DISPLAY_NAMES[tid] for tid in (THEME_CLASSIC, THEME_REDLINE)
        )
        rows = [
            ("Active theme", THEME_DISPLAY_NAMES.get(active, active)),
            ("Available", available),
            (
                "Color mode",
                f"{_color_mode_label(selection.color_mode)} · {_detected_color_label()}",
            ),
            ("Stored at", str(default_theme_path())),
        ]
        if selection.source == "env":
            rows.append(("Note", "MERCURY_THEME overrides host-local file"))
        _print_status_block(rows)
        output.write("")

        actions: list[tuple[str, str]] = []
        if active != THEME_REDLINE:
            actions.append((APPEARANCE_USE_REDLINE, "Use Mercury Redline"))
        if active != THEME_CLASSIC:
            actions.append((APPEARANCE_USE_CLASSIC, "Use Mercury Classic"))
        actions.append((APPEARANCE_PREVIEW, "Preview themes"))
        actions.append((APPEARANCE_COLOR, "Color mode"))
        actions.append((APPEARANCE_RESET, "Reset to default"))

        _write_action_menu(actions)
        selected = _read_action_choice(actions)
        if selected is None:
            return
        if selected == "":
            continue
        if selected == APPEARANCE_USE_REDLINE:
            _apply_theme(THEME_REDLINE)
            continue
        if selected == APPEARANCE_USE_CLASSIC:
            _apply_theme(THEME_CLASSIC)
            continue
        if selected == APPEARANCE_PREVIEW:
            run_theme_preview_menu()
            continue
        if selected == APPEARANCE_COLOR:
            run_color_mode_menu()
            continue
        if selected == APPEARANCE_RESET:
            run_reset_options()
            continue


def _apply_theme(theme_id: str) -> None:
    path = save_theme_selection(theme_id)
    reload_appearance()
    _print_theme_updated(theme_id, path=path, active=active_theme_id())
    menu_prompts.wait_for_continue()


def run_theme_preview_menu() -> None:
    """Synthetic previews only — does not change preference until Apply."""
    while True:
        display_screen.open_screen("THEME PREVIEW")
        output.write("Synthetic gallery · no HDD / package / host-maintenance access")
        output.write("")
        actions = [
            ("preview_redline", "Preview Mercury Redline"),
            ("preview_classic", "Preview Mercury Classic"),
            ("compare", "Compare both themes"),
        ]
        _write_action_menu(actions)
        selected = _read_action_choice(actions)
        if selected is None:
            return
        if selected == "":
            continue
        if selected == "preview_redline":
            _preview_then_offer(THEME_REDLINE)
            continue
        if selected == "preview_classic":
            _preview_then_offer(THEME_CLASSIC)
            continue
        if selected == "compare":
            print_theme_preview(theme_id=THEME_CLASSIC)
            output.write("")
            print_theme_preview(theme_id=THEME_REDLINE)
            menu_prompts.wait_for_continue()
            continue


def _preview_then_offer(theme_id: str) -> None:
    print_theme_preview(theme_id=theme_id)
    output.write("")
    actions = [
        ("apply", "Apply this theme"),
        ("keep", "Return without changing"),
    ]
    _write_action_menu(actions)
    selected = _read_action_choice(actions)
    if selected == "apply":
        _apply_theme(theme_id)


def run_color_mode_menu() -> None:
    """Interactive color-mode preference (NO_COLOR still wins at render time)."""
    while True:
        selection = load_theme_selection()
        display_screen.open_screen("COLOR MODE")
        rows = [
            ("Current", _color_mode_label(selection.color_mode)),
            ("Detected", _detected_color_label().title()),
        ]
        if not colors_enabled():
            rows.append(("Note", "NO_COLOR disables interactive color"))
        _print_status_block(rows)
        output.write("")
        actions: list[tuple[str, str]] = []
        for mode_id, label in COLOR_MODE_CHOICES:
            suffix = " · current" if mode_id == selection.color_mode else ""
            actions.append((mode_id, f"{label}{suffix}"))
        _write_action_menu(actions)
        selected = _read_action_choice(actions)
        if selected is None:
            return
        if selected == "":
            continue
        path = save_color_mode(selected)
        reload_appearance()
        _print_screen_header("Color mode updated")
        _print_status_block(
            [
                ("Color mode", _color_mode_label(selected)),
                ("Stored at", str(path)),
                ("Mercury HDD", "Unchanged"),
            ]
        )
        output.write("")
        menu_prompts.wait_for_continue()


def run_reset_options() -> None:
    """Reset host-local appearance preference only (y/N)."""
    accepted = menu_prompts.ask_yes_no(
        "Reset Mercury appearance settings to defaults?",
        default=False,
    )
    if accepted is not True:
        output.write("Reset cancelled.")
        return
    removed = reset_theme_selection()
    reload_appearance()
    _print_screen_header("Options reset")
    _print_status_block(
        [
            (
                "Active theme",
                THEME_DISPLAY_NAMES.get(active_theme_id(), active_theme_id()),
            ),
            ("Color mode", "Auto"),
            ("Removed", str(removed) if removed else "(no preference file)"),
            ("Mercury HDD", "Unchanged"),
        ]
    )
    output.write("")
    menu_prompts.wait_for_continue()

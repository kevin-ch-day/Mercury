"""Plain-text CLI output with optional Rich theming on TTY (Mercury dark palette)."""

from __future__ import annotations

import sys
from typing import TextIO

from mercury.terminal.theme import (
    action_banner as theme_action_banner,
    colors_enabled,
    field_line,
    hint_text,
    prompt_text,
    report_header,
    section_rule,
    section_title,
    strip_markup,
    tag,
    tag_plain,
)

_stream: TextIO | None = None
_console = None


def _configure_stdio() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except (OSError, ValueError):
                    pass


_configure_stdio()


def set_stream(stream: TextIO | None) -> None:
    """Redirect output (for tests). Pass None to reset to stdout."""
    global _stream, _console
    _stream = stream
    _console = None


def _out() -> TextIO:
    return _stream if _stream is not None else sys.stdout


def _get_console():
    global _console
    if _console is None:
        from rich.console import Console

        from mercury.terminal.theme import rich_theme

        enabled = colors_enabled(stream=_out())
        _console = Console(
            file=_out(),
            force_terminal=enabled,
            no_color=not enabled,
            highlight=False,
            soft_wrap=True,
            theme=rich_theme() if enabled else None,
        )
    return _console


def _looks_like_markup(text: str) -> bool:
    if not colors_enabled(stream=_out()) or "[" not in text or "]" not in text:
        return False
    # Avoid treating plain status lines like "[ok] verified" as markup when unstyled.
    if text.lstrip().startswith(("[ok]", "[--]", "[!!]", "[i]")) and "[/" not in text:
        return False
    return True


def write(text: str = "") -> None:
    if _looks_like_markup(text):
        _get_console().print(text, markup=True, highlight=False)
    else:
        print(text, file=_out())


def rule(width: int = 60, char: str = "-") -> None:
    line = char * width
    if colors_enabled(stream=_out()):
        from mercury.terminal.theme import markup as theme_markup
        from mercury.terminal.theme import RULE

        write(theme_markup(line, RULE))
    else:
        write(line)


def section(title: str) -> None:
    """Visual section break for menu screens and reports."""
    write()
    if colors_enabled(stream=_out()):
        write(section_title(title))
        from mercury.terminal.theme import fancy_rule

        write(f"  {fancy_rule(width=min(60, max(len(title) + 16, 32)))}")
    else:
        write(title)
        write("-" * min(max(len(title), 20), 60))


def action_banner(title: str) -> None:
    """Heading shown when a menu action runs."""
    for line in theme_action_banner(title):
        write(line)


def tag_ok(text: str) -> str:
    return tag_plain("ok", text)


def tag_warn(text: str) -> str:
    return tag_plain("warn", text)


def tag_fail(text: str) -> str:
    return tag_plain("fail", text)


def heading(text: str) -> None:
    write()
    if colors_enabled(stream=_out()):
        write(section_title(text))
    else:
        write(text)


def field(name: str, value: object) -> None:
    write(field_line(name, value))


def bullet(text: str) -> None:
    if colors_enabled(stream=_out()):
        from mercury.terminal.theme import ACCENT, markup as theme_markup, VALUE

        write(f"  {theme_markup('◆', ACCENT)} [{VALUE}]{text}[/{VALUE}]")
    else:
        write(f"  - {text}")


def item(text: str, indent: int = 2) -> None:
    if _looks_like_markup(text):
        write(f"{' ' * indent}{text}")
    elif colors_enabled(stream=_out()) and text.startswith(("[ok]", "[--]", "[!!]", "[i]")):
        kind_map = {"[ok]": "ok", "[--]": "warn", "[!!]": "fail", "[i]": "info"}
        for prefix, kind in kind_map.items():
            if text.startswith(prefix):
                body = text[len(prefix) :].lstrip()
                write(f"{' ' * indent}{tag(kind, body)}")
                return
        write(f"{' ' * indent}{text}")
    else:
        write(f"{' ' * indent}{text}")


def write_report_header(title: str) -> None:
    for line in report_header(title):
        write(line)


def write_hint(text: str) -> None:
    write("")
    write(hint_text(text))


def write_prompt(text: str) -> None:
    write(prompt_text(text))


__all__ = [
    "action_banner",
    "bullet",
    "field",
    "heading",
    "item",
    "rule",
    "section",
    "set_stream",
    "strip_markup",
    "tag_fail",
    "tag_ok",
    "tag_warn",
    "write",
    "write_hint",
    "write_prompt",
    "write_report_header",
]

"""Plain-text CLI output with optional Rich theming on TTY (Mercury dark palette)."""

from __future__ import annotations

import re
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

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|access[_-]?token|token|secret|credential)\b"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SENSITIVE_CLI_OPTION = re.compile(
    r"(?i)(--(?:password|passwd|api-key|access-token|token))(?:=|\s+)([^\s]+)"
)
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_AWS_ACCESS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


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
            # Keep soft_wrap off: True can leave a one-cell styled fragment
            # (often a stray "]") on the next operator line.
            soft_wrap=False,
            theme=rich_theme() if enabled else None,
        )
    return _console


def _looks_like_markup(text: str) -> bool:
    if not colors_enabled(stream=_out()) or "[" not in text:
        return False
    # Only parse as Rich markup when we emitted real style tags. Plain operator
    # lines like "  [prod 1/4] db" or "Next … [5]" must stay literal.
    if "[/" in text or "\\[" in text:
        return True
    return False


def redact_sensitive_text(text: str) -> str:
    """Remove common credential forms before operator-facing output."""
    value = _PRIVATE_KEY_BLOCK.sub("<redacted-private-key>", str(text))
    value = _SENSITIVE_ASSIGNMENT.sub(r"\1\2<redacted>", value)
    value = _SENSITIVE_CLI_OPTION.sub(r"\1=<redacted>", value)
    value = _URL_USERINFO.sub(r"\1<redacted>@", value)
    return _AWS_ACCESS_KEY.sub("<redacted-aws-access-key>", value)


def write(text: str = "") -> None:
    safe_text = redact_sensitive_text(text)
    if _looks_like_markup(safe_text):
        _get_console().print(safe_text, markup=True, highlight=False)
    else:
        # Every caller passes through the credential redactor above. Paths and
        # hardware identifiers intentionally remain visible in this operator CLI.
        print(safe_text, file=_out())  # lgtm[py/clear-text-logging-sensitive-data]


def rule(width: int = 60, char: str | None = None) -> None:
    from mercury.terminal.design_system import active_styles
    from mercury.terminal.theme import markup as theme_markup

    styles = active_styles()
    line = (char or ("-" if not colors_enabled(stream=_out()) else styles.rule_char)) * width
    if colors_enabled(stream=_out()):
        write(theme_markup(line, styles.rule))
    else:
        write(line)


def section(title: str) -> None:
    """Visual section break for menu screens and reports."""
    write()
    write(section_title(title) if colors_enabled(stream=_out()) else title)
    write(section_rule(title, max_width=60))


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
    """Write a muted operator note on the next line (no forced leading blank)."""
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

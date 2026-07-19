"""
Mercury terminal theme — liquid-silver palette for dark (black) backgrounds.

Foreground-only styling: assumes a dark terminal. Disable with ``NO_COLOR``,
``MERCURY_NO_COLOR=1``, or non-TTY stdout. Force with ``MERCURY_FORCE_COLOR=1``
(overrides ``NO_COLOR``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

StatusKind = Literal["ok", "warn", "fail", "info"]

# ── Mercury palette (dark terminal) ─────────────────────────────────────────
# Metallic silver + liquid mercury cyan-teal + deep void rules.
SILVER = "#C8D6E5"
SILVER_BRIGHT = "#E8F1FA"
MERCURY = "#5CE1E6"
MERCURY_GLOW = "#00D4FF"
MERCURY_DEEP = "#2A8B9C"
VOID = "#141A22"
RULE_DARK = "#243044"
RULE_LIGHT = "#3D5570"
VIOLET = "#8B9DC3"
MUTED = "#6B7F99"

# Semantic
OK = "bold #4EECAC"
WARN = "bold #F0C674"
FAIL = "bold #FF7B9C"
INFO = "#7EC8E3"

# UI roles (Rich markup style strings)
TITLE = f"bold {MERCURY_GLOW}"
TITLE_ACCENT = f"bold {MERCURY}"
SUBTITLE = f"italic {VIOLET}"
ACCENT = MERCURY_GLOW
RULE = f"dim {RULE_DARK}"
RULE_GLOW = MERCURY_DEEP
SECTION = f"bold {SILVER_BRIGHT}"
LABEL = SILVER
VALUE = SILVER_BRIGHT
VALUE_MUTED = MUTED
HINT = f"dim italic {MUTED}"
MENU_KEY = f"bold {MERCURY_GLOW}"
MENU_OPTION = SILVER_BRIGHT
MENU_SECTION = f"bold {MERCURY}"
MENU_RULE = RULE
TABLE_HEADER = f"bold {MERCURY_GLOW}"
TABLE_RULE = RULE
PROMPT = f"bold {MERCURY_GLOW}"
BANNER = TITLE
ACTION = f"bold {MERCURY_GLOW}"
GLYPH = MERCURY
SEPARATOR = f"dim {RULE_LIGHT}"

RULE_WIDTH = 62
_READY_BLOCKED_RE = re.compile(r"^(\d+)\s+ready\s·\s+(\d+)\s+blocked$")
_COVERAGE_RE = re.compile(r"^(\d+)/(\d+)\s+source databases$")
_STATUS_WORDS: dict[str, str] = {
    "ready": OK,
    "verified": OK,
    "complete": OK,
    "blocked": WARN,
    "partial": WARN,
    "missing": WARN,
    "unverified": WARN,
    "failed": FAIL,
    "refused": FAIL,
}
_TAG_RE = re.compile(r"^\[(ok|--|!!|i)\](?:\s|$)")
_MENU_REF_RE = re.compile(r"\[(\d+)\]")
_MARKUP_RE = re.compile(r"\[/?[^\]]+\]")

_force_color: bool | None = None


def set_color_enabled(enabled: bool | None) -> None:
    """Override auto color detection (for tests). ``None`` restores auto."""
    global _force_color
    _force_color = enabled


def colors_enabled(*, stream=None) -> bool:
    if _force_color is not None:
        return _force_color
    if os.environ.get("MERCURY_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR") or os.environ.get("MERCURY_NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if stream is None:
        from mercury.core import output

        stream = output._out()
    isatty = getattr(stream, "isatty", lambda: False)
    return bool(isatty())


def rich_theme():
    """Rich ``Theme`` for Console-level defaults."""
    from rich.theme import Theme

    return Theme(
        {
            "info": INFO,
            "warning": WARN,
            "error": FAIL,
            "success": OK,
            "prompt": PROMPT,
            "muted": HINT,
        }
    )


def strip_markup(text: str) -> str:
    if "[" not in text:
        return text
    try:
        from rich.markup import render

        return render(text).plain
    except Exception:
        return _MARKUP_RE.sub("", text)


def markup(text: str, style: str) -> str:
    if not colors_enabled():
        return text
    return f"[{style}]{text}[/{style}]"


def tag_plain(kind: StatusKind, text: str) -> str:
    prefix = {"ok": "[ok]", "warn": "[--]", "fail": "[!!]", "info": "[i]"}[kind]
    return f"{prefix} {text}"


def status_badge(kind: StatusKind) -> str:
    """Compact status badge only (``ok``, ``--``, ``!!``)."""
    plain = {"ok": "ok", "warn": "--", "fail": "!!", "info": "i"}[kind]
    if not colors_enabled():
        return f"[{plain}]"
    style = {"ok": OK, "warn": WARN, "fail": FAIL, "info": INFO}[kind]
    return f"[{style}]{plain}[/]"


def tag(kind: StatusKind, text: str) -> str:
    if not colors_enabled():
        return tag_plain(kind, text)
    return f"{status_badge(kind)} [{VALUE}]{text}[/{VALUE}]"


def rule_line(*, width: int = RULE_WIDTH, char: str = "─") -> str:
    line = char * width
    if not colors_enabled():
        return line
    return markup(line, RULE)


def fancy_rule(*, width: int = RULE_WIDTH) -> str:
    return rule_line(width=width, char="─")


def section_title(title: str) -> str:
    if not colors_enabled():
        return title
    return markup(title, SECTION)


def section_rule(title: str, *, max_width: int = 60) -> str:
    width = min(max(len(title), 16), max_width)
    line = "-" * width
    if not colors_enabled():
        return line
    return markup(line, TABLE_RULE)


def report_header(title: str, *, max_width: int = 60) -> list[str]:
    width = min(RULE_WIDTH, max_width if max_width > 0 else RULE_WIDTH)
    if not colors_enabled():
        return [title, "─" * width]
    return [
        markup(title, SECTION),
        markup("─" * width, TABLE_RULE),
    ]


def field_line(name: str, value: object) -> str:
    if not colors_enabled():
        return f"  {name}: {value}"
    styled = style_inline_value(str(value))
    return f"  [{LABEL}]{name}:[/{LABEL}] {styled}"


def menu_title_line() -> str:
    """Single-line title (legacy callers)."""
    if not colors_enabled():
        return "MERCURY OPERATOR CONSOLE"
    return f"[{TITLE}]MERCURY OPERATOR CONSOLE[/]"


def menu_header_lines(subtitle: str) -> list[str]:
    """Branded menu header block."""
    if not colors_enabled():
        return ["MERCURY OPERATOR CONSOLE", subtitle, "─" * RULE_WIDTH]
    return [
        f"[{TITLE}]MERCURY OPERATOR CONSOLE[/]",
        f"[{SUBTITLE}]{subtitle}[/]",
        markup("─" * RULE_WIDTH, RULE),
    ]


def menu_subtitle_line(text: str) -> str:
    if not colors_enabled():
        return text
    return markup(text, SUBTITLE)


def menu_section_header(name: str, *, indent: int = 2) -> str:
    prefix = " " * indent
    if not colors_enabled():
        return f"{prefix}{name}"
    return f"{prefix}[{MENU_SECTION}]{name}[/]"


def menu_item_line(
    key: str,
    title: str,
    *,
    title_width: int = 0,
    indent: int = 4,
) -> str:
    """Render exactly one action-only menu row.

    Safety details and help belong on the selected action screen.  Keeping this
    primitive to a key and title prevents explanatory continuations from
    quietly returning to menus as the console grows.
    """
    prefix = " " * indent
    key_part = f"[{key}]"
    title_part = title.ljust(title_width) if title_width > 0 else title

    if not colors_enabled():
        label = f"{key_part} {title_part}".rstrip()
        return f"{prefix}{label}"

    styled_key = markup(key_part, MENU_KEY)
    styled_title = markup(title_part, MENU_OPTION)
    label = f"{styled_key} {styled_title}"
    return f"{prefix}{label}"


def menu_bottom_option(label: str, *, indent: int = 6) -> str:
    prefix = " " * indent
    if not colors_enabled():
        return f"{prefix}[0] {label}"
    return (
        f"{prefix}[{MENU_KEY}][0][/] "
        f"[{VALUE_MUTED}]{label}[/]"
    )


def _styled_status_tag(status_tag: str) -> str:
    if status_tag == "[ok]":
        return status_badge("ok")
    if status_tag == "[--]":
        return status_badge("warn")
    if status_tag == "[!!]":
        return status_badge("fail")
    return markup(status_tag, INFO)


def menu_status_row(label: str, status_tag: str, detail: str, *, label_width: int = 10) -> str:
    if not colors_enabled():
        return f"  {label:<{label_width}}{status_tag} {detail}"

    styled_label = markup(f"{label:<{label_width}}", LABEL)
    styled_tag = _styled_status_tag(status_tag)
    styled_detail = style_inline_value(detail)
    return f"  {styled_label}{styled_tag} {styled_detail}"


def style_inline_value(text: str) -> str:
    """Colorize status badges and menu refs embedded in dashboard/field values."""
    if not colors_enabled():
        return text

    match = _TAG_RE.match(text)
    if match:
        kind_map = {"ok": "ok", "--": "warn", "!!": "fail", "i": "info"}
        kind = kind_map[match.group(1)]
        rest = text[match.end() :].strip()
        if rest:
            return f"{status_badge(kind)} [{VALUE}]{rest}[/]"
        return status_badge(kind)

    if text in {"[ok]", "[--]", "[!!]"}:
        kind_map = {"[ok]": "ok", "[--]": "warn", "[!!]": "fail"}
        return status_badge(kind_map[text])

    def _menu_ref(match: re.Match[str]) -> str:
        return f"[{MENU_KEY}][{match.group(1)}][/]"

    styled = _MENU_REF_RE.sub(_menu_ref, text)
    if styled != text:
        return styled

    sync_match = _READY_BLOCKED_RE.match(text)
    if sync_match:
        ready_n, blocked_n = sync_match.groups()
        return (
            f"[{OK}]{ready_n} ready[/] "
            f"[{HINT}]·[/] "
            f"[{WARN}]{blocked_n} blocked[/]"
        )

    coverage_match = _COVERAGE_RE.match(text)
    if coverage_match:
        have, total = coverage_match.groups()
        style = OK if have == total and total != "0" else WARN if have != "0" else FAIL
        return f"[{style}]{have}/{total}[/] [{VALUE_MUTED}]source databases[/]"

    lower = text.lower()
    for word, style in _STATUS_WORDS.items():
        if word == lower or f" {word}" in lower or lower.startswith(word):
            return markup(text, style)

    return markup(text, VALUE)


def dashboard_row(label: str, value: str, *, label_width: int = 22) -> str:
    if not colors_enabled():
        return f"  {label.ljust(label_width)}{value}"
    styled_value = style_inline_value(value)
    return f"  [{LABEL}]{label.ljust(label_width)}[/]{styled_value}"


def action_banner(title: str) -> list[str]:
    width = min(len(title) + 8, 60)
    if not colors_enabled():
        return ["", title, "-" * width]
    return [
        "",
        f"[{ACTION}]{title}[/]",
        markup("─" * width, RULE),
    ]


def open_screen_lines(title: str) -> list[str]:
    if not colors_enabled():
        return ["", title, "─" * RULE_WIDTH]
    return [
        "",
        markup(title, SECTION),
        markup("─" * RULE_WIDTH, RULE),
    ]


def prompt_text(text: str) -> str:
    if not colors_enabled():
        return text
    return markup(text.strip(), PROMPT)


def hint_text(text: str) -> str:
    if not colors_enabled():
        return text
    return markup(text, HINT)


def style_table_cell(cell: str) -> str:
    """Highlight status-like table cells."""
    if not colors_enabled() or not cell:
        return cell
    stripped = cell.strip()
    lower = stripped.lower()
    if lower in {"ready", "verified", "ok", "yes"}:
        return markup(stripped, OK)
    if lower in {"blocked", "missing", "unverified", "no", "dry-run"}:
        return markup(stripped, WARN)
    if lower in {"failed", "refused", "error"}:
        return markup(stripped, FAIL)
    if lower.startswith("prod") or lower.startswith("dev"):
        return markup(stripped, INFO)
    return stripped


def style_table_lines(lines: list[str]) -> list[str]:
    """Apply header/rule styling and highlight status cells in table body."""
    if not colors_enabled() or len(lines) < 2:
        return lines
    header, rule, *body = lines
    indent = len(header) - len(header.lstrip())
    prefix = header[:indent]
    cells = re.split(r"  +", header[indent:].strip())
    styled_header = prefix + "  ".join(markup(cell, TABLE_HEADER) for cell in cells)
    styled_rule = prefix + markup(rule[indent:], TABLE_RULE)

    return [styled_header, styled_rule, *body]


def table_header_line(prefix: str, header_cells: list[str]) -> str:
    line = prefix + "  ".join(header_cells)
    if not colors_enabled():
        return line
    styled_cells = [markup(cell, TABLE_HEADER) for cell in header_cells]
    return prefix + "  ".join(styled_cells)


@dataclass(frozen=True)
class MercuryTheme:
    """Named palette export for documentation and future extension."""

    title: str = TITLE
    subtitle: str = SUBTITLE
    accent: str = ACCENT
    ok: str = OK
    warn: str = WARN
    fail: str = FAIL


DEFAULT_THEME = MercuryTheme()


def body_label(text: str, *, indent: int = 2) -> str:
    prefix = " " * indent
    if not colors_enabled():
        return f"{prefix}{text}"
    return f"{prefix}[{SECTION}]{text}[/]"


def help_line(text: str, *, indent: int = 2) -> str:
    prefix = " " * indent
    if not colors_enabled():
        return f"{prefix}{text}"
    return f"{prefix}[{VALUE_MUTED}]{text}[/]"


def summary_line(text: str) -> str:
    if not colors_enabled():
        return text
    return f"[{VALUE_MUTED}]{text}[/]"


def count_summary_line(text: str) -> str:
    if not colors_enabled():
        return text
    return f"[{VALUE}]{text}[/]"


def submenu_intro() -> str:
    if not colors_enabled():
        return "  Actions"
    return f"  [{SECTION}]Actions[/]"


def submenu_empty_hint() -> str:
    if not colors_enabled():
        return "  Enter a menu number, or 0 to go back."
    return (
        f"  [{HINT}]Enter a menu number, or [/]"
        f"[{MENU_KEY}]0[/][{HINT}] to go back.[/]"
    )


def submenu_block(
    options: list[tuple[str, str]],
    *,
    title: str | None = None,
    bottom_label: str = "Back",
    indent: int = 6,
) -> list[str]:
    """Full styled submenu: optional title, rule, options, return row."""
    lines: list[str] = []
    if title:
        lines.extend(open_screen_lines(title))
    else:
        lines.append("")
    for key, label in options:
        lines.append(menu_item_line(key, label, indent=indent))
    lines.append(menu_bottom_option(bottom_label, indent=indent))
    return lines


def dashboard_panel(rows: list[str]) -> list[str]:
    """Main-menu dashboard content."""
    return rows


def continue_prompt() -> str:
    return prompt_text("\nPress any key to continue...")


def get_theme() -> MercuryTheme:
    return DEFAULT_THEME

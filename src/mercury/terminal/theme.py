"""
Mercury terminal theme — Classic liquid-silver and Mercury Redline.

Foreground styling for dark terminals. Disable with ``NO_COLOR``,
``MERCURY_NO_COLOR=1``, or non-TTY stdout. Force with ``MERCURY_FORCE_COLOR=1``
(overrides ``NO_COLOR``). Select theme with ``MERCURY_THEME`` or host-local
``theme.json`` (never on the Mercury HDD).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from mercury.terminal.color_capability import (
    colors_enabled,
    set_color_enabled as _set_color_enabled,
    set_color_mode_override,
)
from mercury.terminal.design_system import active_styles, clear_style_cache
from mercury.terminal.theme_settings import (
    THEME_CLASSIC,
    THEME_MONOCHROME,
    THEME_REDLINE,
    active_theme_id,
    set_theme_override,
)
from mercury.terminal.theme_tokens import ColorMode, SemanticToken

StatusKind = Literal["ok", "warn", "fail", "info"]

# Minimal Classic fallbacks for Rich Console / MercuryTheme when styles are empty.
OK = "bold #4EECAC"
WARN = "bold #F0C674"
FAIL = "bold #FF7B9C"
INFO = "#7EC8E3"
TITLE = "bold #00D4FF"
SUBTITLE = "italic #8B9DC3"
ACCENT = "#00D4FF"
PROMPT = "bold #00D4FF"
HINT = "dim italic #6B7F99"

RULE_WIDTH = 62
_READY_BLOCKED_RE = re.compile(r"^(\d+)\s+ready\s·\s+(\d+)\s+blocked$")
_COVERAGE_RE = re.compile(r"^(\d+)/(\d+)\s+source databases$")
_TAG_RE = re.compile(r"^\[(ok|--|!!|i|PASS|WARN|FAIL|INFO)\](?:\s|$)")
_MENU_REF_RE = re.compile(r"\[(\d+)\]")
_MARKUP_RE = re.compile(r"\[/?[^\]]+\]")


def set_color_enabled(enabled: bool | None) -> None:
    """Override auto color detection (for tests). ``None`` restores auto."""
    _set_color_enabled(enabled)
    clear_style_cache()
    try:
        from mercury.core import output as out_mod

        # Drop cached Rich Console so color / stream flags re-resolve.
        out_mod._console = None
    except Exception:
        pass


def rich_theme():
    """Rich ``Theme`` for Console-level defaults."""
    from rich.theme import Theme

    s = active_styles()
    return Theme(
        {
            "info": s.info or INFO,
            "warning": s.warn or WARN,
            "error": s.fail or FAIL,
            "success": s.ok or OK,
            "prompt": s.prompt or PROMPT,
            "muted": s.hint or HINT,
        }
    )


def strip_markup(text: str) -> str:
    """Return plain text; never leave Rich escape backslashes visible."""
    if not text:
        return text
    plain = text
    if "[" in text or "\\" in text:
        try:
            from rich.markup import render

            plain = render(text).plain
        except Exception:
            plain = _MARKUP_RE.sub("", text)
    return (
        plain.replace("\\[", "[")
        .replace("\\]", "]")
        .replace("\\\\", "\\")
    )


def markup(text: str, style: str) -> str:
    """Wrap plain text in a Rich style span without leaking ``\\[`` escapes."""
    if not colors_enabled() or not style:
        return text
    body = str(text)
    if "[" not in body and "]" not in body and "\\" not in body:
        return f"[{style}]{body}[/]"
    from rich.text import Text

    return Text(body, style=style).markup


def styled_bracket_label(inner: str, style: str) -> str:
    """Render ``[INNER]`` with styling; brackets never use ``\\[`` escapes."""
    if not colors_enabled() or not style:
        return f"[{inner}]"
    from rich.text import Text

    return Text.assemble(("[", style), (str(inner), style), ("]", style)).markup


def _badges() -> dict[str, str]:
    return dict(active_styles().status_badges)


def tag_plain(kind: StatusKind, text: str) -> str:
    prefix = _badges()[kind]
    return f"{prefix} {text}"


def status_badge(kind: StatusKind) -> str:
    plain = _badges()[kind]
    if not colors_enabled():
        return plain
    s = active_styles()
    style = {"ok": s.ok, "warn": s.warn, "fail": s.fail, "info": s.info}[kind]
    if not style:
        return plain
    return styled_bracket_label(plain.strip("[]"), style)


def tag(kind: StatusKind, text: str) -> str:
    if not colors_enabled():
        return tag_plain(kind, text)
    s = active_styles()
    return f"{status_badge(kind)} {markup(text, s.value)}"


def _clamp_rule_width(width: int) -> int:
    """Avoid soft-wrap leaving a one-cell colored fragment on the next line."""
    try:
        import shutil

        cols = shutil.get_terminal_size(fallback=(width + 1, 24)).columns
        return max(16, min(width, max(16, cols - 1)))
    except Exception:
        return max(16, width)


def rule_line(
    *,
    width: int = RULE_WIDTH,
    char: str | None = None,
    level: Literal["major", "normal", "minor"] = "normal",
) -> str:
    """Width-aware separator (major=Signal Red, normal=Deep Oxide, minor=Steel)."""
    s = active_styles()
    from mercury.terminal.color_capability import unicode_box_supported

    width = _clamp_rule_width(width)
    if char is None:
        if not colors_enabled():
            # Preserve Redline rail hierarchy without color: steel / oxide / signal.
            if s.theme_id == THEME_REDLINE and unicode_box_supported():
                char = {
                    "major": "━",
                    "minor": "┄",
                    "normal": "─",
                }.get(level, "─")
            else:
                char = "─" if unicode_box_supported() else "-"
        elif level == "minor":
            char = s.rule_char_minor or s.rule_char
        elif level == "normal" and s.theme_id == THEME_REDLINE and unicode_box_supported():
            # Thin angular edge for normal panels (thick reserved for major light-line).
            char = "─"
        else:
            char = s.rule_char
    line = char * width
    if not colors_enabled():
        return line
    style = {
        "major": s.rule_major or s.rule,
        "normal": s.rule,
        "minor": s.table_rule or s.separator,
    }.get(level, s.rule)
    if not style:
        return line
    return markup(line, style)

def fancy_rule(*, width: int = RULE_WIDTH) -> str:
    return rule_line(width=width)


def section_title(title: str) -> str:
    """Important section title — legible on dark backgrounds (never dim-only)."""
    if not colors_enabled():
        return title
    s = active_styles()
    if s.theme_id == THEME_REDLINE:
        # Angular marker (geometry), bone-white title — not soft ornaments.
        return markup("▸ ", s.brand_marker) + markup(title, s.value)
    return markup(title, s.section)


def section_rule(title: str, *, max_width: int = 60) -> str:
    width = min(max(len(title), 16), max_width)
    if not colors_enabled():
        from mercury.terminal.color_capability import unicode_box_supported

        return ("─" if unicode_box_supported() else "-") * width
    s = active_styles()
    ch = s.rule_char_minor if s.theme_id == THEME_REDLINE else s.rule_char
    return markup(ch * width, s.table_rule)


def report_header(title: str, *, max_width: int = 60) -> list[str]:
    width = min(RULE_WIDTH, max_width if max_width > 0 else RULE_WIDTH)
    if not colors_enabled():
        from mercury.terminal.color_capability import unicode_box_supported

        ch = "─" if unicode_box_supported() else "-"
        return [title, ch * width]
    s = active_styles()
    return [
        markup(title, s.section),
        markup(s.rule_char * width, s.table_rule),
    ]


def field_line(name: str, value: object) -> str:
    if not colors_enabled():
        return f"  {name}: {value}"
    s = active_styles()
    styled = style_inline_value(str(value))
    # Escape name carefully: field labels rarely have brackets.
    return f"  {markup(f'{name}:', s.label)} {styled}"


def menu_title_line() -> str:
    """Single-line title (legacy callers)."""
    s = active_styles()
    if s.header_variant.startswith("redline"):
        title = "MERCURY // REDLINE"
    else:
        title = "MERCURY OPERATOR CONSOLE"
    if not colors_enabled():
        return title
    return markup(title, s.title)


def menu_header_lines(subtitle: str, *, variant: str | None = None) -> list[str]:
    """Branded menu header block.

    Redline uses one production identity with a dual-rail frame (steel rail +
    signal light-line). Classic keeps the legacy operator-console wording.
    """
    s = active_styles()
    use = variant or s.header_variant
    width = s.rule_width
    from mercury.terminal.color_capability import unicode_box_supported

    if use.startswith("redline"):
        primary = "MERCURY // REDLINE"
        secondary = "BACKUP · RECOVERY · MIGRATION"
    else:
        primary = "MERCURY OPERATOR CONSOLE"
        secondary = subtitle

    if not colors_enabled():
        if use.startswith("redline"):
            # Distinguish rails without color: staccato steel vs solid edge.
            rail = "┄" if unicode_box_supported() else "-"
            edge = "━" if unicode_box_supported() else "="
            return [rail * width, primary, secondary, edge * width]
        ch = "─" if unicode_box_supported() else "-"
        return [primary, secondary, ch * width]

    if use.startswith("redline"):
        primary_styled = (
            markup("MERCURY ", s.title)
            + markup("//", s.brand_marker)
            + markup(" REDLINE", s.title)
        )
        return [
            rule_line(width=width, level="minor"),
            primary_styled,
            markup(secondary, s.subtitle),
            rule_line(width=width, level="major"),
        ]

    return [
        markup(primary, s.title),
        markup(secondary, s.subtitle),
        rule_line(width=width, level="normal"),
    ]


def menu_subtitle_line(text: str) -> str:
    if not colors_enabled():
        return text
    return markup(text, active_styles().subtitle)


def menu_section_header(name: str, *, indent: int = 2) -> str:
    prefix = " " * indent
    if not colors_enabled():
        return f"{prefix}{name}"
    return f"{prefix}{markup(name, active_styles().menu_section)}"


def menu_item_line(
    key: str,
    title: str,
    *,
    title_width: int = 0,
    indent: int = 4,
    recommended: bool = False,
    disabled: bool = False,
    destructive: bool = False,
) -> str:
    """Render exactly one action-only menu row."""
    prefix = " " * indent
    key_part = f"[{key}]"
    s = active_styles()
    is_recommended = recommended or bool(re.search(r"\brecommended\b", title, re.IGNORECASE))
    display_title = title
    marker = ""
    suffix = ""
    if is_recommended and s.theme_id == THEME_REDLINE:
        display_title = re.sub(r"\s+recommended\s*$", "", title, flags=re.IGNORECASE).rstrip()
        marker = "▸ "
        suffix_plain = "          RECOMMENDED"
    else:
        suffix_plain = ""

    title_part = display_title.ljust(title_width) if title_width > 0 else display_title

    if not colors_enabled():
        label = f"{marker}{key_part} {title_part}{suffix_plain}".rstrip()
        return f"{prefix}{label}"

    option_style = s.value_muted if disabled else (s.destructive if destructive else s.menu_option)
    styled_key = styled_bracket_label(key, s.menu_key)
    styled_title = markup(title_part, option_style)
    styled_marker = markup(marker, s.recommended) if marker else ""
    if is_recommended and s.theme_id == THEME_REDLINE:
        suffix = f"  {markup('RECOMMENDED', s.recommended)}"
    return f"{prefix}{styled_marker}{styled_key} {styled_title}{suffix}"


def menu_bottom_option(label: str, *, indent: int = 6) -> str:
    prefix = " " * indent
    s = active_styles()
    if not colors_enabled():
        return f"{prefix}[0] {label}"
    return f"{prefix}{styled_bracket_label('0', s.menu_key)} {markup(label, s.value_muted)}"


def _styled_status_tag(status_tag: str) -> str:
    badges = _badges()
    inverse = {v: k for k, v in badges.items()}
    # Also accept classic tags when viewing redline (and vice versa).
    classic = {"[ok]": "ok", "[--]": "warn", "[!!]": "fail", "[i]": "info"}
    redline = {"[PASS]": "ok", "[WARN]": "warn", "[FAIL]": "fail", "[INFO]": "info"}
    kind = inverse.get(status_tag) or classic.get(status_tag) or redline.get(status_tag)
    if kind:
        return status_badge(kind)  # type: ignore[arg-type]
    return markup(status_tag, active_styles().info)


def menu_status_row(label: str, status_tag: str, detail: str, *, label_width: int = 10) -> str:
    if not colors_enabled():
        return f"  {label:<{label_width}}{status_tag} {detail}"

    s = active_styles()
    styled_label = markup(f"{label:<{label_width}}", s.label)
    styled_tag = _styled_status_tag(status_tag)
    styled_detail = style_inline_value(detail)
    return f"  {styled_label}{styled_tag} {styled_detail}"


def style_inline_value(text: str) -> str:
    """Colorize status badges and menu refs embedded in dashboard/field values."""
    if not colors_enabled():
        return text

    s = active_styles()
    match = _TAG_RE.match(text)
    if match:
        raw = match.group(1)
        kind_map = {
            "ok": "ok",
            "--": "warn",
            "!!": "fail",
            "i": "info",
            "PASS": "ok",
            "WARN": "warn",
            "FAIL": "fail",
            "INFO": "info",
        }
        kind = kind_map[raw]
        rest = text[match.end() :].strip()
        if rest:
            return f"{status_badge(kind)} {markup(rest, s.value)}"  # type: ignore[arg-type]
        return status_badge(kind)  # type: ignore[arg-type]

    if text in {"[ok]", "[--]", "[!!]", "[PASS]", "[WARN]", "[FAIL]", "[INFO]"}:
        return _styled_status_tag(text)

    def _menu_ref(match: re.Match[str]) -> str:
        return styled_bracket_label(match.group(1), s.menu_key)

    if _MENU_REF_RE.search(text):
        # Rebuild with Text so nested markup never double-escapes brackets.
        from rich.text import Text

        parts: list[tuple[str, str]] = []
        last = 0
        for match in _MENU_REF_RE.finditer(text):
            if match.start() > last:
                parts.append((text[last : match.start()], s.value))
            parts.append((f"[{match.group(1)}]", s.menu_key))
            last = match.end()
        if last < len(text):
            parts.append((text[last:], s.value))
        return Text.assemble(*parts).markup

    sync_match = _READY_BLOCKED_RE.match(text)
    if sync_match:
        ready_n, blocked_n = sync_match.groups()
        from rich.text import Text

        return Text.assemble(
            (f"{ready_n} ready", s.ok),
            (" · ", s.hint),
            (f"{blocked_n} blocked", s.warn),
        ).markup

    coverage_match = _COVERAGE_RE.match(text)
    if coverage_match:
        have, total = coverage_match.groups()
        style = s.ok if have == total and total != "0" else s.warn if have != "0" else s.fail
        from rich.text import Text

        return Text.assemble(
            (f"{have}/{total}", style),
            (" ", ""),
            ("source databases", s.value_muted),
        ).markup

    # Compound dashboard values: colorize formal ALL-CAPS tokens only.
    if " · " in text:
        formal = {
            "VERIFIED": s.verified or s.ok,
            "DETACHED": s.value_muted,
            "READ-ONLY": s.read_only,
            "FAILED": s.fail,
            "REFUSED": s.fail,
            "CONNECTED": s.ok,
            "MOUNTED": s.ok,
        }
        from rich.text import Text

        pieces: list[tuple[str, str]] = []
        for index, part in enumerate(text.split(" · ")):
            if index:
                pieces.append((" · ", s.separator or s.hint))
            if part in formal:
                pieces.append((part, formal[part]))
            else:
                pieces.append((part, s.value))
        return Text.assemble(*pieces).markup

    lower = text.lower()
    status_words = {
        "ready": s.ok,
        "verified": s.verified or s.ok,
        "complete": s.ok,
        "blocked": s.warn,
        "partial": s.warn,
        "missing": s.warn,
        "unverified": s.warn,
        "failed": s.fail,
        "refused": s.fail,
        "recommended": s.recommended,
        "detached": s.value_muted,
        "read-only": s.read_only,
        "read only": s.read_only,
    }
    for word, style in status_words.items():
        if word == lower:
            return markup(text, style)

    return markup(text, s.value)

def dashboard_row(label: str, value: str, *, label_width: int = 14) -> str:
    if not colors_enabled():
        return f"  {label.ljust(label_width)}{value}"
    s = active_styles()
    styled_value = style_inline_value(value)
    return f"  [{s.label}]{label.ljust(label_width)}[/]{styled_value}"


def system_state_header() -> list[str]:
    """Section chrome for SYSTEM STATE dashboards."""
    s = active_styles()
    title = "SYSTEM STATE"
    if not colors_enabled():
        from mercury.terminal.color_capability import unicode_box_supported

        ch = "─" if unicode_box_supported() else "-"
        return [title, ch * s.rule_width]
    return [
        section_title(title),
        rule_line(width=s.rule_width, level="normal"),
    ]


def action_banner(title: str) -> list[str]:
    s = active_styles()
    width = s.rule_width
    if not colors_enabled():
        return ["", title, "-" * width]
    return [
        "",
        markup(title, s.action),
        rule_line(width=width, level="normal"),
    ]


def danger_banner(title: str) -> list[str]:
    """Destructive confirmation heading (bright danger; full-width major rule)."""
    s = active_styles()
    width = s.rule_width
    if not colors_enabled():
        return [title, rule_line(width=width, level="major")]
    return [
        markup(title, s.destructive),
        rule_line(width=width, level="major"),
    ]


def open_screen_lines(title: str) -> list[str]:
    s = active_styles()
    if not colors_enabled():
        return ["", title, rule_line(width=RULE_WIDTH, level="normal")]
    return [
        "",
        section_title(title) if s.theme_id == THEME_REDLINE else markup(title, s.section),
        rule_line(width=RULE_WIDTH, level="normal"),
    ]


def important_banner(title: str = "IMPORTANT") -> list[str]:
    """Advisory warning frame (Deep Oxide — not bright danger red)."""
    s = active_styles()
    width = s.rule_width
    if not colors_enabled():
        return [title, rule_line(width=width, level="normal")]
    # Redline: Bone White title + Deep Oxide (normal) rule — not Signal Red.
    title_style = s.value if s.theme_id == THEME_REDLINE else (s.section or s.value)
    return [
        markup(title, title_style),
        rule_line(width=width, level="normal"),
    ]
def prompt_text(text: str) -> str:
    if not colors_enabled():
        return text
    s = active_styles()
    leading_len = len(text) - len(text.lstrip(" \t\n\r"))
    trailing_len = len(text) - len(text.rstrip(" \t\n\r"))
    leading = text[:leading_len]
    trailing = text[len(text) - trailing_len :] if trailing_len else ""
    body = text[leading_len : len(text) - trailing_len if trailing_len else None]
    if not body:
        return text
    return f"{leading}{markup(body, s.prompt)}{trailing}"


def hint_text(text: str) -> str:
    if not colors_enabled():
        return text
    return markup(text, active_styles().hint)


def style_table_cell(cell: str) -> str:
    """Highlight status-like table cells."""
    if not colors_enabled() or not cell:
        return cell
    s = active_styles()
    stripped = cell.strip()
    lower = stripped.lower()
    if lower in {"ready", "verified", "ok", "yes", "fresh", "pass"}:
        return markup(stripped, s.ok)
    if lower in {"blocked", "missing", "unverified", "no", "dry-run", "warn"}:
        return markup(stripped, s.warn)
    if lower in {"failed", "refused", "error", "fail"}:
        return markup(stripped, s.fail)
    if lower.startswith("prod") or lower.startswith("dev"):
        return markup(stripped, s.info)
    return stripped


def style_table_lines(lines: list[str]) -> list[str]:
    """Apply header/rule styling without destroying fixed column padding."""
    if not colors_enabled() or len(lines) < 2:
        return lines
    s = active_styles()
    header, rule, *body = lines
    indent = len(header) - len(header.lstrip())
    prefix = header[:indent]
    styled_header = prefix + markup(header[indent:], s.table_header)
    styled_rule = prefix + markup(rule[indent:], s.table_rule)
    return [styled_header, styled_rule, *body]


def table_header_line(prefix: str, header_cells: list[str]) -> str:
    line = prefix + "  ".join(header_cells)
    if not colors_enabled():
        return line
    s = active_styles()
    styled_cells = [markup(cell, s.table_header) for cell in header_cells]
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
    theme_id: str = THEME_CLASSIC


DEFAULT_THEME = MercuryTheme()


def body_label(text: str, *, indent: int = 2) -> str:
    prefix = " " * indent
    if not colors_enabled():
        return f"{prefix}{text}"
    return f"{prefix}{markup(text, active_styles().section)}"


def help_line(text: str, *, indent: int = 2) -> str:
    prefix = " " * indent
    if not colors_enabled():
        return f"{prefix}{text}"
    return f"{prefix}{markup(text, active_styles().value_muted)}"


def summary_line(text: str) -> str:
    if not colors_enabled():
        return text
    return markup(text, active_styles().value_muted)


def count_summary_line(text: str) -> str:
    if not colors_enabled():
        return text
    return markup(text, active_styles().value)


def submenu_intro() -> str:
    if not colors_enabled():
        return "  Actions"
    return f"  {markup('Actions', active_styles().section)}"


def submenu_empty_hint() -> str:
    if not colors_enabled():
        return "  Enter a menu number, or 0 to go back."
    s = active_styles()
    return (
        f"  {markup('Enter a menu number, or ', s.hint)}"
        f"{markup('0', s.menu_key)}"
        f"{markup(' to go back.', s.hint)}"
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
    for key, label in options:
        lines.append(menu_item_line(key, label, indent=indent))
    lines.append(menu_bottom_option(bottom_label, indent=indent))
    return lines


def dashboard_panel(rows: list[str]) -> list[str]:
    """Main-menu dashboard content."""
    return rows


def continue_prompt() -> str:
    return prompt_text("\nPress Enter to continue...")


def get_theme() -> MercuryTheme:
    s = active_styles()
    return MercuryTheme(
        title=s.title or TITLE,
        subtitle=s.subtitle or SUBTITLE,
        accent=s.accent or ACCENT,
        ok=s.ok or OK,
        warn=s.warn or WARN,
        fail=s.fail or FAIL,
        theme_id=s.theme_id,
    )


# Public re-exports for design-system consumers
__all__ = [
    "THEME_CLASSIC",
    "THEME_MONOCHROME",
    "THEME_REDLINE",
    "ColorMode",
    "SemanticToken",
    "active_theme_id",
    "colors_enabled",
    "set_color_enabled",
    "set_color_mode_override",
    "set_theme_override",
]

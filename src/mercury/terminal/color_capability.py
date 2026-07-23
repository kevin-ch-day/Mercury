"""Terminal color capability detection (presentation-only)."""

from __future__ import annotations

import os
import sys
from typing import TextIO

from mercury.terminal.theme_tokens import ColorMode

_force_color: bool | None = None
_force_mode: ColorMode | None = None


def set_color_enabled(enabled: bool | None) -> None:
    """Override auto color detection (for tests). ``None`` restores auto."""
    global _force_color
    _force_color = enabled


def set_color_mode_override(mode: ColorMode | None) -> None:
    """Force a color mode for tests. ``None`` restores auto."""
    global _force_mode
    _force_mode = mode


def colors_enabled(*, stream: TextIO | None = None) -> bool:
    if _force_color is not None:
        return _force_color
    if os.environ.get("MERCURY_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR") or os.environ.get("MERCURY_NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    mode = requested_color_mode()
    if mode == ColorMode.NONE:
        return False
    if stream is None:
        stream = sys.stdout
    isatty = getattr(stream, "isatty", lambda: False)
    return bool(isatty())


def requested_color_mode() -> ColorMode:
    """Resolve preferred color mode: env wins, then host-local preference, else auto."""
    raw = (os.environ.get("MERCURY_COLOR_MODE") or "").strip().lower()
    if raw:
        aliases = {
            "auto": ColorMode.AUTO,
            "truecolor": ColorMode.TRUECOLOR,
            "true": ColorMode.TRUECOLOR,
            "24bit": ColorMode.TRUECOLOR,
            "256": ColorMode.ANSI256,
            "ansi256": ColorMode.ANSI256,
            "16": ColorMode.ANSI16,
            "ansi16": ColorMode.ANSI16,
            "none": ColorMode.NONE,
            "off": ColorMode.NONE,
            "monochrome": ColorMode.NONE,
            "mono": ColorMode.NONE,
        }
        return aliases.get(raw, ColorMode.AUTO)
    try:
        from mercury.terminal.theme_settings import preferred_color_mode_id, parse_color_mode

        return parse_color_mode(preferred_color_mode_id())
    except Exception:
        return ColorMode.AUTO


def detect_color_mode(*, stream: TextIO | None = None) -> ColorMode:
    """Resolve the effective color mode for styled output."""
    if _force_mode is not None:
        return _force_mode
    if not colors_enabled(stream=stream):
        return ColorMode.NONE

    requested = requested_color_mode()
    if requested != ColorMode.AUTO:
        return requested

    term = (os.environ.get("TERM") or "").lower()
    if term in {"", "dumb"}:
        # Colors were explicitly enabled (force flag or TTY probe earlier).
        return ColorMode.ANSI16

    colorterm = (os.environ.get("COLORTERM") or "").lower()
    if "truecolor" in colorterm or "24bit" in colorterm:
        return ColorMode.TRUECOLOR
    if "256color" in term or term.endswith("-256color"):
        return ColorMode.ANSI256
    return ColorMode.ANSI16


def unicode_box_supported(*, stream: TextIO | None = None) -> bool:
    """True when heavy Unicode rules are safe to emit."""
    if os.environ.get("MERCURY_ASCII_RULES") == "1":
        return False
    encoding = getattr(stream or sys.stdout, "encoding", None) or "utf-8"
    try:
        "━".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False

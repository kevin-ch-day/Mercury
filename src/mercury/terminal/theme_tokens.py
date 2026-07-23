"""Mercury Redline / Classic semantic color tokens and palette mappings.

Presentation-only. Operational code must never import palette hex values for
logic — only terminal renderers resolve tokens through the active theme profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class SemanticToken(str, Enum):
    BACKGROUND = "BACKGROUND"
    SURFACE = "SURFACE"
    SURFACE_RAISED = "SURFACE_RAISED"
    BORDER = "BORDER"
    BORDER_ACTIVE = "BORDER_ACTIVE"

    TEXT_PRIMARY = "TEXT_PRIMARY"
    TEXT_SECONDARY = "TEXT_SECONDARY"
    TEXT_MUTED = "TEXT_MUTED"
    TEXT_DISABLED = "TEXT_DISABLED"

    ACCENT_PRIMARY = "ACCENT_PRIMARY"
    ACCENT_BRIGHT = "ACCENT_BRIGHT"
    ACCENT_DARK = "ACCENT_DARK"
    ACCENT_EMBER = "ACCENT_EMBER"

    STATUS_SUCCESS = "STATUS_SUCCESS"
    STATUS_WARNING = "STATUS_WARNING"
    STATUS_DANGER = "STATUS_DANGER"
    STATUS_INFO = "STATUS_INFO"
    STATUS_NEUTRAL = "STATUS_NEUTRAL"

    ACTION_PRIMARY = "ACTION_PRIMARY"
    ACTION_SECONDARY = "ACTION_SECONDARY"
    ACTION_DISABLED = "ACTION_DISABLED"
    ACTION_DESTRUCTIVE = "ACTION_DESTRUCTIVE"

    STATE_ACTIVE = "STATE_ACTIVE"
    STATE_PAUSED = "STATE_PAUSED"
    STATE_DETACHED = "STATE_DETACHED"
    STATE_READ_ONLY = "STATE_READ_ONLY"
    STATE_VERIFIED = "STATE_VERIFIED"


class ColorMode(str, Enum):
    AUTO = "auto"
    TRUECOLOR = "truecolor"
    ANSI256 = "256"
    ANSI16 = "16"
    NONE = "none"


THEME_CLASSIC = "mercury-classic"
THEME_REDLINE = "mercury-redline"
THEME_MONOCHROME = "mercury-monochrome"

KNOWN_THEMES: tuple[str, ...] = (THEME_CLASSIC, THEME_REDLINE, THEME_MONOCHROME)

THEME_DISPLAY_NAMES: Mapping[str, str] = {
    THEME_CLASSIC: "Mercury Classic",
    THEME_REDLINE: "Mercury Redline",
    THEME_MONOCHROME: "Mercury Monochrome",
}


@dataclass(frozen=True)
class PaletteColor:
    """One named swatch with progressive fallbacks."""

    name: str
    truecolor: str
    ansi256: int
    ansi16: str  # Rich/ANSI name, e.g. "bright_red"
    mono: str = ""  # unused; monochrome drops color


# Mercury Redline true-color palette (authoritative product swatches).
#
# Design inspiration (Mercury-owned; not a franchise recreation):
# - Digital geometry: angular light-lines on void, red as structure not decoration,
#   circuit-edge rails, high contrast, secondary detail suppressed.
# - Industrial minimalism: monochrome foundation, containerized marks ([PASS]),
#   uniform weights, spare typography, crimson used sparingly for authority.
REDLINE_SWATCHES: Mapping[str, PaletteColor] = {
    "void_black": PaletteColor("Void Black", "#050505", 232, "black"),
    "carbon": PaletteColor("Carbon", "#0C0C0D", 233, "black"),
    "graphite": PaletteColor("Graphite", "#151517", 234, "black"),
    "raised_graphite": PaletteColor("Raised Graphite", "#1D1D20", 235, "black"),
    # Cooler steel — metallic circuit-edge (less warm gray).
    "steel_border": PaletteColor("Steel Border", "#3A3A42", 238, "bright_black"),
    "bone_white": PaletteColor("Bone White", "#E8E4DE", 253, "white"),
    "cold_white": PaletteColor("Cold White", "#F4F2EE", 255, "bright_white"),
    "ash": PaletteColor("Ash", "#A29E99", 247, "white"),
    "dim_ash": PaletteColor("Dim Ash", "#6E6B68", 242, "bright_black"),
    "disabled": PaletteColor("Disabled", "#4A4847", 239, "bright_black"),
    "mercury_crimson": PaletteColor("Mercury Crimson", "#D71920", 160, "red"),
    "signal_red": PaletteColor("Signal Red", "#FF2A32", 196, "bright_red"),
    "deep_oxide": PaletteColor("Deep Oxide", "#7A1117", 88, "red"),
    "ember": PaletteColor("Ember", "#FF4A1C", 202, "bright_red"),
    "dark_ember": PaletteColor("Dark Ember", "#A92713", 124, "red"),
    "success": PaletteColor("Success", "#A8D68D", 150, "green"),
    "warning": PaletteColor("Warning", "#FFB000", 214, "yellow"),
    "danger": PaletteColor("Danger", "#FF3640", 203, "bright_red"),
    "information": PaletteColor("Information", "#AFC7D8", 152, "cyan"),
    "read_only": PaletteColor("Read-only", "#B8A7D9", 183, "magenta"),
}

# Operator-facing design principles for theme preview / documentation receipts.
REDLINE_DESIGN_PRINCIPLES: tuple[str, ...] = (
    "Void foundation — black reads as structure, not empty space",
    "Angular light-lines — red edges define panels; never flood backgrounds",
    "Separator hierarchy — Signal / Oxide / Steel (bright red is rare)",
    "Containerized marks — [PASS] [WARN] [FAIL] [INFO] stay legible without color",
    "Industrial type — geometric titles; descriptive states stay title-case",
    "Restraint — no animation, no glitch noise, no full-red screens",
)


# Classic liquid-silver swatches (preserve prior Mercury look).
CLASSIC_SWATCHES: Mapping[str, PaletteColor] = {
    "silver": PaletteColor("Silver", "#C8D6E5", 252, "white"),
    "silver_bright": PaletteColor("Silver Bright", "#E8F1FA", 255, "bright_white"),
    "mercury": PaletteColor("Mercury", "#5CE1E6", 80, "cyan"),
    "mercury_glow": PaletteColor("Mercury Glow", "#00D4FF", 45, "bright_cyan"),
    "mercury_deep": PaletteColor("Mercury Deep", "#2A8B9C", 30, "cyan"),
    "void": PaletteColor("Void", "#141A22", 234, "black"),
    "rule_dark": PaletteColor("Rule Dark", "#243044", 236, "bright_black"),
    "rule_light": PaletteColor("Rule Light", "#3D5570", 60, "blue"),
    "violet": PaletteColor("Violet", "#8B9DC3", 110, "blue"),
    "muted": PaletteColor("Muted", "#6B7F99", 67, "bright_black"),
    "ok": PaletteColor("OK", "#4EECAC", 86, "green"),
    "warn": PaletteColor("Warn", "#F0C674", 221, "yellow"),
    "fail": PaletteColor("Fail", "#FF7B9C", 211, "bright_red"),
    "info": PaletteColor("Info", "#7EC8E3", 117, "cyan"),
}


def resolve_color(swatch: PaletteColor, mode: ColorMode) -> str:
    """Return a Rich style color fragment for the active color mode."""
    if mode == ColorMode.NONE:
        return ""
    if mode == ColorMode.ANSI16:
        return swatch.ansi16
    if mode == ColorMode.ANSI256:
        return f"color({swatch.ansi256})"
    # truecolor / auto resolved to truecolor by capability layer
    return swatch.truecolor


def redline_token_swatch(token: SemanticToken) -> PaletteColor:
    """Map semantic tokens onto Mercury Redline swatches."""
    s = REDLINE_SWATCHES
    mapping: dict[SemanticToken, PaletteColor] = {
        SemanticToken.BACKGROUND: s["void_black"],
        SemanticToken.SURFACE: s["carbon"],
        SemanticToken.SURFACE_RAISED: s["raised_graphite"],
        SemanticToken.BORDER: s["steel_border"],
        SemanticToken.BORDER_ACTIVE: s["mercury_crimson"],
        SemanticToken.TEXT_PRIMARY: s["bone_white"],
        SemanticToken.TEXT_SECONDARY: s["ash"],
        SemanticToken.TEXT_MUTED: s["dim_ash"],
        SemanticToken.TEXT_DISABLED: s["disabled"],
        SemanticToken.ACCENT_PRIMARY: s["mercury_crimson"],
        SemanticToken.ACCENT_BRIGHT: s["signal_red"],
        SemanticToken.ACCENT_DARK: s["deep_oxide"],
        SemanticToken.ACCENT_EMBER: s["ember"],
        SemanticToken.STATUS_SUCCESS: s["success"],
        SemanticToken.STATUS_WARNING: s["warning"],
        SemanticToken.STATUS_DANGER: s["danger"],
        SemanticToken.STATUS_INFO: s["information"],
        SemanticToken.STATUS_NEUTRAL: s["ash"],
        SemanticToken.ACTION_PRIMARY: s["mercury_crimson"],
        SemanticToken.ACTION_SECONDARY: s["ash"],
        SemanticToken.ACTION_DISABLED: s["disabled"],
        SemanticToken.ACTION_DESTRUCTIVE: s["danger"],
        SemanticToken.STATE_ACTIVE: s["signal_red"],
        SemanticToken.STATE_PAUSED: s["warning"],
        SemanticToken.STATE_DETACHED: s["dim_ash"],
        SemanticToken.STATE_READ_ONLY: s["read_only"],
        SemanticToken.STATE_VERIFIED: s["success"],
    }
    return mapping[token]


def classic_token_swatch(token: SemanticToken) -> PaletteColor:
    """Map semantic tokens onto Classic liquid-silver swatches."""
    s = CLASSIC_SWATCHES
    mapping: dict[SemanticToken, PaletteColor] = {
        SemanticToken.BACKGROUND: s["void"],
        SemanticToken.SURFACE: s["void"],
        SemanticToken.SURFACE_RAISED: s["rule_dark"],
        SemanticToken.BORDER: s["rule_dark"],
        SemanticToken.BORDER_ACTIVE: s["mercury_glow"],
        SemanticToken.TEXT_PRIMARY: s["silver_bright"],
        SemanticToken.TEXT_SECONDARY: s["silver"],
        SemanticToken.TEXT_MUTED: s["muted"],
        SemanticToken.TEXT_DISABLED: s["muted"],
        SemanticToken.ACCENT_PRIMARY: s["mercury_glow"],
        SemanticToken.ACCENT_BRIGHT: s["mercury"],
        SemanticToken.ACCENT_DARK: s["mercury_deep"],
        SemanticToken.ACCENT_EMBER: s["mercury"],
        SemanticToken.STATUS_SUCCESS: s["ok"],
        SemanticToken.STATUS_WARNING: s["warn"],
        SemanticToken.STATUS_DANGER: s["fail"],
        SemanticToken.STATUS_INFO: s["info"],
        SemanticToken.STATUS_NEUTRAL: s["muted"],
        SemanticToken.ACTION_PRIMARY: s["mercury_glow"],
        SemanticToken.ACTION_SECONDARY: s["silver"],
        SemanticToken.ACTION_DISABLED: s["muted"],
        SemanticToken.ACTION_DESTRUCTIVE: s["fail"],
        SemanticToken.STATE_ACTIVE: s["mercury"],
        SemanticToken.STATE_PAUSED: s["warn"],
        SemanticToken.STATE_DETACHED: s["muted"],
        SemanticToken.STATE_READ_ONLY: s["violet"],
        SemanticToken.STATE_VERIFIED: s["ok"],
    }
    return mapping[token]

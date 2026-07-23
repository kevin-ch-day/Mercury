"""Authoritative Mercury terminal design system (Classic + Redline + Monochrome)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mercury.terminal.color_capability import detect_color_mode, unicode_box_supported
from mercury.terminal.theme_settings import active_theme_id
from mercury.terminal.theme_tokens import (
    THEME_CLASSIC,
    THEME_MONOCHROME,
    THEME_REDLINE,
    ColorMode,
    SemanticToken,
    classic_token_swatch,
    redline_token_swatch,
    resolve_color,
)


@dataclass(frozen=True)
class StyleBundle:
    """Resolved Rich style strings for one theme × color-mode combination."""

    theme_id: str
    color_mode: ColorMode

    title: str
    title_accent: str
    subtitle: str
    accent: str
    rule: str  # normal section boundary (Deep Oxide / classic void)
    rule_major: str  # product / danger / failure frames (Signal Red)
    rule_glow: str
    section: str
    label: str
    value: str
    value_muted: str
    hint: str
    menu_key: str
    menu_option: str
    menu_section: str
    menu_rule: str
    table_header: str
    table_rule: str  # minor / table separators (Steel)
    prompt: str
    banner: str
    action: str
    glyph: str
    separator: str
    ok: str
    warn: str
    fail: str
    info: str
    recommended: str
    destructive: str
    read_only: str
    verified: str
    brand_marker: str
    important_frame: str  # IMPORTANT advisory frame (Deep Oxide)

    status_badges: Mapping[str, str]
    rule_char: str
    rule_width: int = 62
    header_variant: str = "classic"  # classic | redline_a


def _rich(style_bits: str, *, bold: bool = False, italic: bool = False, dim: bool = False) -> str:
    parts: list[str] = []
    if bold:
        parts.append("bold")
    if italic:
        parts.append("italic")
    if dim:
        parts.append("dim")
    if style_bits:
        parts.append(style_bits)
    return " ".join(parts) if parts else ""


def _token_color(
    token: SemanticToken,
    *,
    theme_id: str,
    mode: ColorMode,
) -> str:
    if theme_id == THEME_MONOCHROME or mode == ColorMode.NONE:
        return ""
    if theme_id == THEME_REDLINE:
        swatch = redline_token_swatch(token)
    else:
        swatch = classic_token_swatch(token)
    return resolve_color(swatch, mode)


def build_style_bundle(
    *,
    theme_id: str | None = None,
    color_mode: ColorMode | None = None,
) -> StyleBundle:
    tid = theme_id or active_theme_id()
    mode = color_mode if color_mode is not None else detect_color_mode()
    if tid == THEME_MONOCHROME:
        mode = ColorMode.NONE

    def c(token: SemanticToken) -> str:
        return _token_color(token, theme_id=tid, mode=mode)

    use_unicode = unicode_box_supported() and mode != ColorMode.NONE

    if tid == THEME_REDLINE:
        rule_char = "━" if use_unicode else "="
        badges = {
            "ok": "[PASS]",
            "warn": "[WARN]",
            "fail": "[FAIL]",
            "info": "[INFO]",
        }
        return StyleBundle(
            theme_id=tid,
            color_mode=mode,
            title=_rich(c(SemanticToken.TEXT_PRIMARY), bold=True),
            title_accent=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
            subtitle=_rich(c(SemanticToken.TEXT_SECONDARY), italic=True),
            accent=c(SemanticToken.ACCENT_PRIMARY),
            # Separator hierarchy: Signal Red / Deep Oxide / Steel (no dim red titles).
            rule=_rich(c(SemanticToken.ACCENT_DARK)),  # Deep Oxide — normal sections
            rule_major=_rich(c(SemanticToken.ACCENT_BRIGHT)),  # Signal Red — major
            rule_glow=c(SemanticToken.ACCENT_DARK),
            section=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),  # Mercury Crimson
            label=c(SemanticToken.TEXT_SECONDARY),
            value=c(SemanticToken.TEXT_PRIMARY),
            value_muted=c(SemanticToken.TEXT_MUTED),
            hint=_rich(c(SemanticToken.TEXT_MUTED), italic=True, dim=True),
            menu_key=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
            menu_option=c(SemanticToken.TEXT_PRIMARY),
            menu_section=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
            menu_rule=_rich(c(SemanticToken.ACCENT_DARK)),
            table_header=_rich(c(SemanticToken.TEXT_PRIMARY), bold=True),
            table_rule=_rich(c(SemanticToken.BORDER), dim=True),  # Steel — minor
            prompt=_rich(c(SemanticToken.ACCENT_BRIGHT), bold=True),
            banner=_rich(c(SemanticToken.TEXT_PRIMARY), bold=True),
            action=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
            glyph=c(SemanticToken.ACCENT_PRIMARY),
            separator=_rich(c(SemanticToken.BORDER), dim=True),
            ok=_rich(c(SemanticToken.STATUS_SUCCESS), bold=True),
            warn=_rich(c(SemanticToken.STATUS_WARNING), bold=True),
            fail=_rich(c(SemanticToken.STATUS_DANGER), bold=True),
            info=c(SemanticToken.STATUS_INFO),
            recommended=_rich(c(SemanticToken.ACCENT_BRIGHT), bold=True),
            destructive=_rich(c(SemanticToken.ACTION_DESTRUCTIVE), bold=True),
            read_only=c(SemanticToken.STATE_READ_ONLY),
            verified=_rich(c(SemanticToken.STATE_VERIFIED), bold=True),
            brand_marker=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
            important_frame=_rich(c(SemanticToken.ACCENT_DARK)),
            status_badges=badges,
            rule_char=rule_char,
            header_variant="redline_a",
        )

    # Classic (default) and monochrome share classic badge labels.
    rule_char = "─" if use_unicode and tid != THEME_MONOCHROME else "-"
    badges = {"ok": "[ok]", "warn": "[--]", "fail": "[!!]", "info": "[i]"}
    return StyleBundle(
        theme_id=tid,
        color_mode=mode,
        title=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        title_accent=_rich(c(SemanticToken.ACCENT_BRIGHT), bold=True),
        subtitle=_rich(c(SemanticToken.STATE_READ_ONLY), italic=True),
        accent=c(SemanticToken.ACCENT_PRIMARY),
        rule=_rich(c(SemanticToken.BORDER), dim=True),
        rule_major=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        rule_glow=c(SemanticToken.ACCENT_DARK),
        section=_rich(c(SemanticToken.TEXT_PRIMARY), bold=True),
        label=c(SemanticToken.TEXT_SECONDARY),
        value=c(SemanticToken.TEXT_PRIMARY),
        value_muted=c(SemanticToken.TEXT_MUTED),
        hint=_rich(c(SemanticToken.TEXT_MUTED), italic=True, dim=True),
        menu_key=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        menu_option=c(SemanticToken.TEXT_PRIMARY),
        menu_section=_rich(c(SemanticToken.ACCENT_BRIGHT), bold=True),
        menu_rule=_rich(c(SemanticToken.BORDER), dim=True),
        table_header=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        table_rule=_rich(c(SemanticToken.BORDER), dim=True),
        prompt=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        banner=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        action=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        glyph=c(SemanticToken.ACCENT_PRIMARY),
        separator=_rich(c(SemanticToken.BORDER), dim=True),
        ok=_rich(c(SemanticToken.STATUS_SUCCESS), bold=True),
        warn=_rich(c(SemanticToken.STATUS_WARNING), bold=True),
        fail=_rich(c(SemanticToken.STATUS_DANGER), bold=True),
        info=c(SemanticToken.STATUS_INFO),
        recommended=_rich(c(SemanticToken.ACCENT_BRIGHT), bold=True),
        destructive=_rich(c(SemanticToken.ACTION_DESTRUCTIVE), bold=True),
        read_only=c(SemanticToken.STATE_READ_ONLY),
        verified=_rich(c(SemanticToken.STATE_VERIFIED), bold=True),
        brand_marker=_rich(c(SemanticToken.ACCENT_PRIMARY), bold=True),
        important_frame=_rich(c(SemanticToken.BORDER), dim=True),
        status_badges=badges,
        rule_char=rule_char,
        header_variant="classic",
    )


_cached: StyleBundle | None = None
_cached_key: tuple[str, str] | None = None


def clear_style_cache() -> None:
    global _cached, _cached_key
    _cached = None
    _cached_key = None


def active_styles() -> StyleBundle:
    """Return the cached style bundle for the current theme and color mode."""
    global _cached, _cached_key
    theme_id = active_theme_id()
    mode = detect_color_mode()
    key = (theme_id, mode.value)
    if _cached is not None and _cached_key == key:
        return _cached
    _cached = build_style_bundle(theme_id=theme_id, color_mode=mode)
    _cached_key = key
    return _cached


def style_for(token: SemanticToken) -> str:
    """Resolve a semantic token to a Rich style string for the active theme."""
    styles = active_styles()
    mapping = {
        SemanticToken.ACCENT_PRIMARY: styles.accent,
        SemanticToken.STATUS_SUCCESS: styles.ok,
        SemanticToken.STATUS_WARNING: styles.warn,
        SemanticToken.STATUS_DANGER: styles.fail,
        SemanticToken.STATUS_INFO: styles.info,
        SemanticToken.TEXT_PRIMARY: styles.value,
        SemanticToken.TEXT_SECONDARY: styles.label,
        SemanticToken.TEXT_MUTED: styles.value_muted,
        SemanticToken.TEXT_DISABLED: styles.value_muted,
        SemanticToken.ACTION_DESTRUCTIVE: styles.destructive,
        SemanticToken.STATE_VERIFIED: styles.verified,
        SemanticToken.STATE_READ_ONLY: styles.read_only,
        SemanticToken.BORDER_ACTIVE: styles.rule,
    }
    return mapping.get(token, styles.value)

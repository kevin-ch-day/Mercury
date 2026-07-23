"""Host-local theme / appearance preference (never stored on the Mercury HDD)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from mercury.terminal.theme_tokens import (
    KNOWN_THEMES,
    THEME_CLASSIC,
    THEME_DISPLAY_NAMES,
    THEME_MONOCHROME,
    THEME_REDLINE,
    ColorMode,
)

ENV_THEME = "MERCURY_THEME"
ENV_THEME_PATH = "MERCURY_THEME_PATH"

_force_theme: str | None = None

_COLOR_MODE_ALIASES: dict[str, ColorMode] = {
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

COLOR_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("auto", "Auto"),
    ("truecolor", "Truecolor"),
    ("256", "256 color"),
    ("16", "16 color"),
    ("none", "No color"),
)


@dataclass(frozen=True)
class ThemeSelection:
    theme_id: str
    source: str  # env | file | default | override
    path: Path | None = None
    color_mode: str = "auto"  # preference key (auto|truecolor|256|16|none)


def default_theme_path() -> Path:
    override = os.environ.get(ENV_THEME_PATH)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "mercury" / "theme.json"
    return Path.home() / ".local" / "share" / "mercury" / "theme.json"


def set_theme_override(theme_id: str | None) -> None:
    """Test seam: force active theme without touching disk. ``None`` clears."""
    global _force_theme
    _force_theme = theme_id


def validate_theme_id(theme_id: str) -> str:
    normalized = (theme_id or "").strip().lower()
    if normalized not in KNOWN_THEMES:
        known = ", ".join(KNOWN_THEMES)
        raise ValueError(f"Unknown theme {theme_id!r}. Known themes: {known}")
    return normalized


def validate_color_mode_id(color_mode: str) -> str:
    normalized = (color_mode or "auto").strip().lower()
    if normalized not in _COLOR_MODE_ALIASES:
        known = ", ".join(k for k, _ in COLOR_MODE_CHOICES)
        raise ValueError(f"Unknown color mode {color_mode!r}. Known: {known}")
    # Canonical keys used in preference file / menus.
    canonical = {
        "true": "truecolor",
        "24bit": "truecolor",
        "ansi256": "256",
        "ansi16": "16",
        "off": "none",
        "monochrome": "none",
        "mono": "none",
    }.get(normalized, normalized)
    return canonical


def parse_color_mode(color_mode: str) -> ColorMode:
    return _COLOR_MODE_ALIASES[validate_color_mode_id(color_mode)]


def _read_preference_file(settings_path: Path) -> dict:
    if not settings_path.is_file():
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_theme_selection(*, path: Path | None = None) -> ThemeSelection:
    settings_path = path or default_theme_path()
    file_data = _read_preference_file(settings_path)
    file_color = "auto"
    if file_data.get("color_mode"):
        try:
            file_color = validate_color_mode_id(str(file_data.get("color_mode")))
        except ValueError:
            file_color = "auto"

    if _force_theme is not None:
        return ThemeSelection(
            theme_id=_force_theme,
            source="override",
            path=None,
            color_mode=file_color,
        )

    env = (os.environ.get(ENV_THEME) or "").strip().lower()
    if env:
        try:
            return ThemeSelection(
                theme_id=validate_theme_id(env),
                source="env",
                path=None,
                color_mode=file_color,
            )
        except ValueError:
            pass

    if settings_path.is_file() and file_data:
        try:
            theme_id = validate_theme_id(str(file_data.get("theme_id") or THEME_CLASSIC))
            return ThemeSelection(
                theme_id=theme_id,
                source="file",
                path=settings_path,
                color_mode=file_color,
            )
        except ValueError:
            pass

    return ThemeSelection(
        theme_id=THEME_CLASSIC,
        source="default",
        path=settings_path,
        color_mode=file_color if settings_path.is_file() else "auto",
    )


def active_theme_id(*, path: Path | None = None) -> str:
    return load_theme_selection(path=path).theme_id


def preferred_color_mode_id(*, path: Path | None = None) -> str:
    """Host-local color mode preference (env MERCURY_COLOR_MODE wins separately)."""
    return load_theme_selection(path=path).color_mode


def save_theme_selection(
    theme_id: str,
    *,
    path: Path | None = None,
    color_mode: str | None = None,
) -> Path:
    """Persist theme (and optional color mode) on the local host — not the Mercury HDD."""
    validated = validate_theme_id(theme_id)
    settings_path = path or default_theme_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_preference_file(settings_path)
    if color_mode is None:
        raw = existing.get("color_mode", "auto")
        try:
            mode_id = validate_color_mode_id(str(raw))
        except ValueError:
            mode_id = "auto"
    else:
        mode_id = validate_color_mode_id(color_mode)
    payload = {
        "theme_id": validated,
        "display_name": THEME_DISPLAY_NAMES.get(validated, validated),
        "color_mode": mode_id,
    }
    tmp_path = settings_path.with_name(settings_path.name + ".tmp")
    text = json.dumps(payload, indent=2) + "\n"
    tmp_path.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, settings_path)
    try:
        os.chmod(settings_path, 0o600)
    except OSError:
        pass
    return settings_path


def save_color_mode(color_mode: str, *, path: Path | None = None) -> Path:
    """Update only the color-mode preference; preserve active theme id."""
    selection = load_theme_selection(path=path)
    return save_theme_selection(
        selection.theme_id,
        path=path or selection.path or default_theme_path(),
        color_mode=color_mode,
    )


def reset_theme_selection(*, path: Path | None = None) -> Path | None:
    """Remove host-local appearance preference (theme + color mode → defaults)."""
    settings_path = path or default_theme_path()
    if settings_path.is_file():
        settings_path.unlink()
        return settings_path
    return None


def list_themes() -> list[tuple[str, str, bool]]:
    """Return ``(theme_id, display_name, is_active)``."""
    active = active_theme_id()
    return [
        (theme_id, THEME_DISPLAY_NAMES[theme_id], theme_id == active)
        for theme_id in KNOWN_THEMES
    ]


def reload_appearance() -> str:
    """Clear style caches so the next render uses the current preference."""
    from mercury.terminal.design_system import clear_style_cache
    from mercury.terminal.theme import set_color_enabled

    clear_style_cache()
    # Drop forced color override so capability re-reads env + preference.
    set_color_enabled(None)
    return active_theme_id()


__all__ = [
    "COLOR_MODE_CHOICES",
    "ENV_THEME",
    "ENV_THEME_PATH",
    "THEME_CLASSIC",
    "THEME_MONOCHROME",
    "THEME_REDLINE",
    "ThemeSelection",
    "active_theme_id",
    "default_theme_path",
    "list_themes",
    "load_theme_selection",
    "parse_color_mode",
    "preferred_color_mode_id",
    "reload_appearance",
    "reset_theme_selection",
    "save_color_mode",
    "save_theme_selection",
    "set_theme_override",
    "validate_color_mode_id",
    "validate_theme_id",
]

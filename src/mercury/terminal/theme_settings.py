"""Host-local theme selection (never stored on the Mercury HDD)."""

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
)

ENV_THEME = "MERCURY_THEME"
ENV_THEME_PATH = "MERCURY_THEME_PATH"

_force_theme: str | None = None


@dataclass(frozen=True)
class ThemeSelection:
    theme_id: str
    source: str  # env | file | default
    path: Path | None = None


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


def load_theme_selection(*, path: Path | None = None) -> ThemeSelection:
    if _force_theme is not None:
        return ThemeSelection(theme_id=_force_theme, source="override", path=None)

    env = (os.environ.get(ENV_THEME) or "").strip().lower()
    if env:
        try:
            return ThemeSelection(theme_id=validate_theme_id(env), source="env", path=None)
        except ValueError:
            # Invalid env must not crash the console — fall through to file/default.
            pass

    settings_path = path or default_theme_path()
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            theme_id = validate_theme_id(str(data.get("theme_id") or THEME_CLASSIC))
            return ThemeSelection(theme_id=theme_id, source="file", path=settings_path)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    return ThemeSelection(theme_id=THEME_CLASSIC, source="default", path=settings_path)


def active_theme_id(*, path: Path | None = None) -> str:
    return load_theme_selection(path=path).theme_id


def save_theme_selection(theme_id: str, *, path: Path | None = None) -> Path:
    """Persist theme choice on the local host (not the Mercury HDD)."""
    validated = validate_theme_id(theme_id)
    settings_path = path or default_theme_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "theme_id": validated,
        "display_name": THEME_DISPLAY_NAMES.get(validated, validated),
    }
    settings_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return settings_path


def reset_theme_selection(*, path: Path | None = None) -> Path | None:
    """Remove host-local theme preference (reverts to classic / env)."""
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


__all__ = [
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
    "reset_theme_selection",
    "save_theme_selection",
    "set_theme_override",
    "validate_theme_id",
]

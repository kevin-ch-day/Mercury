"""Startup Options / Appearance menus — host-local theme preference only."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mercury.menu.intent import (
    INTENT_BACKUP_SYNC,
    INTENT_BROWSE,
    INTENT_OPTIONS,
    INTENT_SAFE_DISCONNECT,
    OUTCOME_CANCELLED,
    build_startup_intent_options,
    dispatch_startup_intent,
    recommended_startup_action,
)
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance
from mercury.terminal.color_capability import detect_color_mode, set_color_mode_override
from mercury.terminal.design_system import active_styles, clear_style_cache
from mercury.terminal.theme import menu_header_lines, set_color_enabled, strip_markup
from mercury.terminal.theme_settings import (
    THEME_CLASSIC,
    THEME_REDLINE,
    active_theme_id,
    load_theme_selection,
    preferred_color_mode_id,
    reload_appearance,
    reset_theme_selection,
    save_color_mode,
    save_theme_selection,
    set_theme_override,
    validate_theme_id,
)
from mercury.terminal.theme_tokens import ColorMode


@pytest.fixture(autouse=True)
def _theme_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MERCURY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(tmp_path / "host.json"))
    monkeypatch.delenv("MERCURY_THEME", raising=False)
    monkeypatch.delenv("MERCURY_COLOR_MODE", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MERCURY_NO_COLOR", raising=False)
    monkeypatch.delenv("MERCURY_FORCE_COLOR", raising=False)
    set_theme_override(None)
    set_color_enabled(None)
    set_color_mode_override(None)
    clear_style_cache()
    yield
    set_theme_override(None)
    set_color_enabled(None)
    set_color_mode_override(None)
    clear_style_cache()


def _verified_host(**overrides) -> HostMaintenanceState:
    data = dict(
        storage_availability="detaching",
        writes_allowed=False,
        active_write_role="none",
        package_id="pkg_test",
        package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        destination_rehearsal_active=True,
        destination_rehearsal_in_progress=True,
        source_detach_preparation=True,
    )
    data.update(overrides)
    state = HostMaintenanceState(**data)
    save_host_maintenance(state)
    return state


def test_options_appears_on_startup_intent() -> None:
    _verified_host()
    opts = build_startup_intent_options()
    actions = [a for _k, _l, a in opts]
    assert INTENT_OPTIONS in actions
    assert actions[-1] == INTENT_OPTIONS
    assert actions[0] == INTENT_SAFE_DISCONNECT
    key = next(k for k, _l, a in opts if a == INTENT_OPTIONS)
    assert key == str(len(opts))  # last numbered entry


def test_options_available_when_detached() -> None:
    _verified_host(storage_availability="detached", writes_allowed=False)
    opts = build_startup_intent_options()
    assert INTENT_OPTIONS in [a for _k, _l, a in opts]


def test_options_available_when_writes_enabled_software_path() -> None:
    _verified_host(writes_allowed=True, storage_availability="attached", active_write_role="primary")
    opts = build_startup_intent_options()
    assert INTENT_OPTIONS in [a for _k, _l, a in opts]


def test_missing_theme_file_resolves_classic(tmp_path: Path) -> None:
    path = tmp_path / "theme.json"
    assert not path.exists()
    sel = load_theme_selection(path=path)
    assert sel.theme_id == THEME_CLASSIC
    assert sel.source == "default"


def test_env_override_beats_preference_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_CLASSIC, path=path)
    monkeypatch.setenv("MERCURY_THEME", THEME_REDLINE)
    sel = load_theme_selection(path=path)
    assert sel.theme_id == THEME_REDLINE
    assert sel.source == "env"


def test_no_color_beats_theme_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    save_theme_selection(THEME_REDLINE, color_mode="truecolor")
    reload_appearance()
    assert detect_color_mode().value == "none"
    assert active_theme_id() == THEME_REDLINE


def test_redline_selection_writes_only_host_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "theme.json"
    hdd = tmp_path / "fake_hdd"
    hdd.mkdir()
    monkeypatch.setenv("MERCURY_PRIMARY_MOUNT", str(hdd))
    before = {p.name for p in hdd.iterdir()}
    save_theme_selection(THEME_REDLINE, path=path)
    reload_appearance()
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme_id"] == THEME_REDLINE
    assert data["color_mode"] == "auto"
    assert oct(path.stat().st_mode & 0o777) in {"0o600", "0o644"}  # umask may soften
    assert {p.name for p in hdd.iterdir()} == before


def test_classic_selection_and_atomic_write(tmp_path: Path) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_REDLINE, path=path)
    save_theme_selection(THEME_CLASSIC, path=path)
    assert not path.with_name(path.name + ".tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["theme_id"] == THEME_CLASSIC


def test_invalid_theme_refuses() -> None:
    with pytest.raises(ValueError):
        validate_theme_id("not-a-theme")


def test_theme_applies_on_next_render(tmp_path: Path) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_REDLINE, path=path)
    reload_appearance()
    set_color_enabled(False)
    clear_style_cache()
    header = menu_header_lines("ignored")
    joined = "\n".join(strip_markup(line) for line in header)
    assert "MERCURY // REDLINE" in joined
    assert "BACKUP · RECOVERY · MIGRATION" in joined


def test_preview_does_not_change_preference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_CLASSIC, path=path)
    before = path.read_text(encoding="utf-8")
    from mercury.terminal.theme_preview import render_theme_preview

    render_theme_preview(THEME_REDLINE, width=80, force_color=False)
    assert path.read_text(encoding="utf-8") == before
    assert active_theme_id() == THEME_CLASSIC


def test_reset_restores_classic_auto(tmp_path: Path) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_REDLINE, path=path, color_mode="256")
    removed = reset_theme_selection(path=path)
    assert removed == path
    assert not path.exists()
    reload_appearance()
    assert active_theme_id() == THEME_CLASSIC
    assert preferred_color_mode_id(path=path) == "auto"


def test_color_mode_independent_of_theme(tmp_path: Path) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_REDLINE, path=path)
    save_color_mode("16", path=path)
    sel = load_theme_selection(path=path)
    assert sel.theme_id == THEME_REDLINE
    assert sel.color_mode == "16"


def test_dispatch_options_returns_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"ok": False}

    def _fake() -> None:
        called["ok"] = True

    monkeypatch.setattr("mercury.menu.options_menu.run_options_menu", _fake)
    assert dispatch_startup_intent(INTENT_OPTIONS) == OUTCOME_CANCELLED
    assert called["ok"] is True


def test_theme_switch_does_not_change_recommendation() -> None:
    _verified_host()
    before_rec = recommended_startup_action()
    before_order = [a for _k, _l, a in build_startup_intent_options() if a != INTENT_OPTIONS]
    save_theme_selection(THEME_REDLINE)
    reload_appearance()
    after_rec = recommended_startup_action()
    after_order = [a for _k, _l, a in build_startup_intent_options() if a != INTENT_OPTIONS]
    assert before_rec == after_rec == INTENT_SAFE_DISCONNECT
    assert before_order == after_order
    assert INTENT_BACKUP_SYNC in after_order
    assert INTENT_BROWSE in after_order


def test_host_maintenance_fingerprint_unchanged(tmp_path: Path) -> None:
    host = tmp_path / "host.json"
    _verified_host()
    # Re-point was already set by fixture; rewrite known content
    text = host.read_text(encoding="utf-8") if host.exists() else Path(
        os.environ["MERCURY_HOST_MAINTENANCE_PATH"]
    ).read_text(encoding="utf-8")
    path = Path(os.environ["MERCURY_HOST_MAINTENANCE_PATH"])
    before = path.read_bytes()
    save_theme_selection(THEME_REDLINE)
    reload_appearance()
    assert path.read_bytes() == before


def test_classic_and_redline_startup_snapshots() -> None:
    _verified_host()
    save_theme_selection(THEME_CLASSIC)
    reload_appearance()
    set_color_enabled(False)
    clear_style_cache()
    classic = "\n".join(menu_header_lines("Database Backup, Sync, and Disaster Recovery Utility"))
    assert "MERCURY OPERATOR CONSOLE" in classic
    save_theme_selection(THEME_REDLINE)
    reload_appearance()
    set_color_enabled(False)
    clear_style_cache()
    redline = "\n".join(
        strip_markup(line) for line in menu_header_lines("ignored")
    )
    assert "MERCURY // REDLINE" in redline
    assert "BACKUP · RECOVERY · MIGRATION" in redline
    opts = build_startup_intent_options()
    assert any(a == INTENT_OPTIONS for _k, _l, a in opts)


def test_cli_theme_set_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    monkeypatch.setenv("MERCURY_THEME_PATH", str(tmp_path / "theme.json"))
    runner = CliRunner()
    result = runner.invoke(app, ["theme", "set", THEME_REDLINE])
    assert result.exit_code == 0
    assert THEME_REDLINE in result.stdout
    assert "saved" in result.stdout.lower() or "Theme set" in result.stdout
    assert load_theme_selection().theme_id == THEME_REDLINE


def test_options_never_recommended() -> None:
    _verified_host()
    assert recommended_startup_action() != INTENT_OPTIONS
    for _k, label, action in build_startup_intent_options():
        if action == INTENT_OPTIONS:
            assert "recommended" not in label.lower()


def test_options_available_without_host_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Software-only / missing host-maintenance still offers Options."""
    missing = tmp_path / "missing-host.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(missing))
    assert not missing.exists()
    opts = build_startup_intent_options()
    assert INTENT_OPTIONS in [a for _k, _l, a in opts]


def test_preview_does_not_touch_host_or_hdd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "theme.json"
    save_theme_selection(THEME_CLASSIC, path=path)
    host_path = Path(os.environ["MERCURY_HOST_MAINTENANCE_PATH"])
    _verified_host()
    before_host = host_path.read_bytes()
    before_theme = path.read_text(encoding="utf-8")
    hdd = tmp_path / "fake_hdd"
    hdd.mkdir()
    monkeypatch.setenv("MERCURY_PRIMARY_MOUNT", str(hdd))
    before_hdd = {p.name for p in hdd.iterdir()}

    from mercury.terminal.theme_preview import render_theme_preview

    render_theme_preview(THEME_REDLINE, width=80, force_color=False)
    assert path.read_text(encoding="utf-8") == before_theme
    assert host_path.read_bytes() == before_host
    assert {p.name for p in hdd.iterdir()} == before_hdd


def test_apply_theme_reports_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "theme.json"
    monkeypatch.setenv("MERCURY_THEME", THEME_CLASSIC)
    monkeypatch.setattr("mercury.menu.prompts.wait_for_continue", lambda: None)
    captured: list[str] = []
    monkeypatch.setattr("mercury.output.write", lambda s: captured.append(str(s)))

    from mercury.menu.options_menu import _apply_theme

    _apply_theme(THEME_REDLINE)
    joined = "\n".join(strip_markup(line) for line in captured)
    assert "Theme preference saved" in joined
    assert "MERCURY_THEME" in joined
    assert json.loads(path.read_text(encoding="utf-8"))["theme_id"] == THEME_REDLINE
    assert active_theme_id() == THEME_CLASSIC


def test_options_menu_is_flat_three_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Options has theme / color / reset only — no Display preferences hub."""
    captured: list[str] = []
    monkeypatch.setattr(
        "mercury.output.write",
        lambda s: captured.append(str(s)),
    )
    monkeypatch.setattr(
        "mercury.menu.options_menu.display_screen.open_screen",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda *_a, **_k: "0",
    )

    from mercury.menu.options_menu import run_options_menu

    run_options_menu()
    joined = "\n".join(strip_markup(line) for line in captured)
    assert "Change or preview theme" in joined
    assert "Change color mode" in joined
    assert "Reset appearance" in joined
    assert "Display preferences" not in joined
    assert "Scope" in joined
    assert "Host-local" in joined


def test_color_mode_opens_from_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"color": 0}
    monkeypatch.setattr(
        "mercury.menu.options_menu.run_color_mode_menu",
        lambda: calls.__setitem__("color", calls["color"] + 1),
    )
    answers = iter(["2", "0"])  # Change color mode, then Back
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda *_a, **_k: next(answers),
    )
    monkeypatch.setattr("mercury.output.write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "mercury.menu.options_menu.display_screen.open_screen",
        lambda *_a, **_k: None,
    )

    from mercury.menu.options_menu import run_options_menu

    run_options_menu()
    assert calls["color"] == 1


def test_display_preferences_alias_opens_color_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"color": 0}
    monkeypatch.setattr(
        "mercury.menu.options_menu.run_color_mode_menu",
        lambda: calls.__setitem__("color", calls["color"] + 1),
    )

    from mercury.menu.options_menu import run_display_preferences_menu

    # Alias should call color mode directly (which we stubbed).
    # Replace the function body path: alias invokes run_color_mode_menu.
    run_display_preferences_menu()
    assert calls["color"] == 1


def test_startup_snapshot_includes_options_no_color() -> None:
    from mercury.menu.intent import (
        render_startup_intent_context,
        run_startup_intent_chooser,
    )

    _verified_host()
    save_theme_selection(THEME_REDLINE)
    reload_appearance()
    set_color_enabled(False)
    clear_style_cache()
    header = "\n".join(
        strip_markup(line) for line in menu_header_lines("ignored")
    )
    assert "MERCURY // REDLINE" in header
    context = "\n".join(
        strip_markup(line) for line in render_startup_intent_context()
    )
    assert "writes disabled" in context
    opts = build_startup_intent_options()
    labels = "\n".join(f"[{k}] {l}" for k, l, _a in opts)
    assert "[5] Options" in labels or any(
        a == INTENT_OPTIONS and k == "5" for k, _l, a in opts
    )
    # Ensure chooser wiring still imports cleanly.
    assert callable(run_startup_intent_chooser)


def test_health_appearance_uses_shared_options_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"n": 0}
    monkeypatch.setattr(
        "mercury.menu.options_menu.run_appearance_menu",
        lambda: called.__setitem__("n", called["n"] + 1),
    )
    answers = iter(["5", "0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda *_a, **_k: next(answers),
    )
    monkeypatch.setattr("mercury.output.write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "mercury.terminal.screen.open_screen",
        lambda *_a, **_k: None,
    )

    from mercury.menu.task_menus import run_health_hub

    run_health_hub()
    assert called["n"] == 1


def test_cli_theme_set_warns_when_env_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    monkeypatch.setenv("MERCURY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setenv("MERCURY_THEME", THEME_CLASSIC)
    runner = CliRunner()
    result = runner.invoke(app, ["theme", "set", THEME_REDLINE])
    assert result.exit_code == 0
    assert "MERCURY_THEME overrides" in result.stdout
    assert load_theme_selection().theme_id == THEME_CLASSIC
    assert json.loads((tmp_path / "theme.json").read_text(encoding="utf-8"))[
        "theme_id"
    ] == THEME_REDLINE

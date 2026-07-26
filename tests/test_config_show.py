"""Tests for observe-only ``mercury config show``."""

from __future__ import annotations

from typer.testing import CliRunner

from mercury.cli import app


def test_config_show_is_observe_only() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    out = result.stdout
    assert "Local configuration" in out
    assert "local.toml" in out
    assert "password" not in out.lower() or "Never commit" in out
    assert "config init" in out

"""Contract tests for the safe first-run setup script."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_setup_script_has_valid_bash_syntax_and_help() -> None:
    script = REPO_ROOT / "setup.sh"
    syntax = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr

    result = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--init-config" in result.stdout
    assert "--system-site-packages" in result.stdout


def test_setup_script_keeps_host_and_data_changes_out_of_default_bootstrap() -> None:
    source = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")

    assert "Mercury bootstrap complete." in source
    assert "This bootstrap never mounts storage" in source
    assert "sudo " not in source
    assert "dnf install" not in source
    assert "mariadb-dump" not in source
    assert '"$VENV/bin/mercury" config init' in source
    assert 'if [[ "$INIT_CONFIG" -eq 1 ]]; then' in source

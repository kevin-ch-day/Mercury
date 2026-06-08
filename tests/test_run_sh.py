"""Tests for the repo launcher shell script."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _make_launcher_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(REPO_ROOT / "run.sh", repo / "run.sh")
    (repo / "src").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'mercury'\n", encoding="utf-8")
    (repo / ".venv" / "bin").mkdir(parents=True)
    return repo


def test_run_sh_reports_clear_bootstrap_recovery_message(tmp_path: Path) -> None:
    repo = _make_launcher_repo(tmp_path)
    _write_executable(
        repo / ".venv" / "bin" / "pip",
        """#!/usr/bin/env bash
echo "WARNING: Retrying after connection broken by NameResolutionError: https://pypi.org/simple/hatchling/" >&2
echo "ERROR: Could not find a version that satisfies the requirement hatchling" >&2
exit 1
""",
    )

    result = subprocess.run(
        ["bash", "run.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Mercury bootstrap failed while installing Python dependencies." in combined
    assert "MERCURY_SKIP_SYNC=1 ./run.sh" in combined
    assert "--system-site-packages" in combined
    assert "/mnt/MERCURY_DATA_USB/mercury_backups" in combined


def test_run_sh_uses_existing_venv_when_skip_sync_is_enabled(tmp_path: Path) -> None:
    repo = _make_launcher_repo(tmp_path)
    _write_executable(
        repo / ".venv" / "bin" / "mercury",
        """#!/usr/bin/env bash
echo "mercury-called:$*"
""",
    )
    _write_executable(
        repo / ".venv" / "bin" / "pip",
        """#!/usr/bin/env bash
echo "pip-should-not-run"
exit 99
""",
    )

    env = os.environ.copy()
    env["MERCURY_SKIP_SYNC"] = "1"
    result = subprocess.run(
        ["bash", "run.sh", "db", "ping"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert "mercury-called:db ping" in result.stdout
    assert "pip-should-not-run" not in (result.stdout + result.stderr)

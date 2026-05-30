"""CLI behavior in seed mode."""

import subprocess
import sys


def test_backup_plan_requires_demo_in_seed() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "backup", "plan"],
        capture_output=True,
        text=True,
        cwd=None,
    )
    assert result.returncode != 0
    assert "demo" in (result.stdout + result.stderr).lower()


def test_classify_does_not_import_discover_at_startup() -> None:
    """classify should work without loading discovery at cli import."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mercury.cli",
            "db",
            "classify",
            "--name",
            "erebus_threat_intel_prod",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "production" in result.stdout

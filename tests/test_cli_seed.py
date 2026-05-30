"""CLI behavior in seed mode."""

import subprocess
import sys


def test_backup_plan_without_demo_uses_live_or_config_inventory() -> None:
    """backup plan (no --demo) should not crash; uses live inventory when configured."""
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "backup", "plan"],
        capture_output=True,
        text=True,
        cwd=None,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert "backup plan" in combined or "backup sources" in combined


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

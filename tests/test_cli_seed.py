"""CLI behavior in seed mode."""

from tests.conftest import run_cli


def test_backup_plan_without_demo_uses_live_or_config_inventory() -> None:
    """backup plan (no --demo) should not crash; uses live inventory when configured."""
    result = run_cli("backup", "plan")
    assert result.returncode == 0, result.stdout + result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert "backup plan" in combined or "backup sources" in combined


def test_classify_does_not_import_discover_at_startup() -> None:
    """classify should work without loading discovery at cli import."""
    result = run_cli(
        "db",
        "classify",
        "--name",
        "erebus_threat_intel_prod",
    )
    assert result.returncode == 0
    assert "production" in result.stdout


def test_status_default_scope_is_clean() -> None:
    result = run_cli("status")
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "inventory: 5 databases" in combined.lower()
    assert "random_test_db" not in combined
    assert "_restorecheck_" not in combined

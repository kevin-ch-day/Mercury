"""CLI integration and seed-mode command tests."""

from __future__ import annotations

from tests.conftest import run_cli

# merged from test_cli_seed.py
def test_backup_plan_without_demo_uses_live_or_config_inventory() -> None:
    """backup plan (no --demo) should not crash; uses live inventory when configured."""
    result = run_cli("backup", "plan")
    assert result.returncode == 0, result.stdout + result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert "backup plan" in combined or "backup sources" in combined

# merged from test_cli_seed.py
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

# merged from test_cli_seed.py
def test_status_default_scope_is_clean() -> None:
    result = run_cli("status")
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "active scope: 6 databases" in combined.lower()
    assert "random_test_db" not in combined
    assert "_restorecheck_" not in combined

# merged from test_cli_m4.py
def test_cli_schema_plan_demo() -> None:
    result = run_cli("backup", "schema-plan", "--demo")
    assert result.returncode == 0
    assert "SCHEMA-ONLY BACKUP PLAN" in result.stdout

# merged from test_cli_m4.py
def test_cli_manifest_preview_schema_only() -> None:
    result = run_cli(
        "backup",
        "manifest-preview",
        "--db",
        "erebus_threat_intel_prod",
        "--kind",
        "schema_only",
    )
    assert result.returncode == 0
    assert ".schema.sql.gz" in result.stdout
    assert '"dry_run": true' in result.stdout

# merged from test_cli_m4.py
def test_cli_manifest_preview_full() -> None:
    result = run_cli(
        "backup",
        "manifest-preview",
        "--db",
        "erebus_threat_intel_prod",
        "--kind",
        "full",
    )
    assert result.returncode == 0
    assert ".sql.gz" in result.stdout

# merged from test_cli_m4.py
def test_cli_manifest_preview_rejects_dev() -> None:
    result = run_cli(
        "backup",
        "manifest-preview",
        "--db",
        "erebus_threat_intel_dev",
        "--kind",
        "full",
    )
    assert result.returncode != 0
    assert "backup source" in (result.stdout + result.stderr).lower()


def test_cli_backup_verify_exposes_explicit_dev_recovery_gate() -> None:
    result = run_cli("backup", "verify", "--help")
    assert result.returncode == 0
    # Rich truncates long option labels at the fixed test-console width.
    assert "allow-developmen" in result.stdout
    assert "configured optional" in result.stdout

# merged from test_cli_m4.py
def test_cli_db_active() -> None:
    result = run_cli("db", "active")
    assert result.returncode == 0 or "local.toml" in (result.stdout + result.stderr).lower()

# merged from test_cli_m4.py
def test_cli_sync_run_help_does_not_offer_yes_bypass() -> None:
    result = run_cli("sync", "run", "--help")
    assert result.returncode == 0
    assert "--yes" not in result.stdout
    assert "SYNC DEV" in result.stdout


def test_cli_migration_readiness_commands_are_registered() -> None:
    result = run_cli("migration", "--help")
    assert result.returncode == 0
    assert "blockers" in result.stdout
    assert "next" in result.stdout

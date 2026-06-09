"""CLI smoke tests for M4 commands."""

from tests.conftest import run_cli


def test_cli_schema_plan_demo() -> None:
    result = run_cli("backup", "schema-plan", "--demo")
    assert result.returncode == 0
    assert "SCHEMA-ONLY BACKUP PLAN" in result.stdout


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


def test_cli_db_active() -> None:
    result = run_cli("db", "active")
    assert result.returncode == 0 or "local.toml" in (result.stdout + result.stderr).lower()


def test_cli_sync_run_help_does_not_offer_yes_bypass() -> None:
    result = run_cli("sync", "run", "--help")
    assert result.returncode == 0
    assert "--yes" not in result.stdout
    assert "SYNC DEV" in result.stdout

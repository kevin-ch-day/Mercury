"""Fail-closed SQL identifier quoting for interpolated MariaDB names."""

from __future__ import annotations

import pytest

from mercury.backup.backup_runner import BackupExecutionError, assert_safe_backup_source
from mercury.backup.dump_planner import build_dump_argv, build_planned_dump_command
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.identifiers import quote_ident, sql_schema_literal
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.deploy.models import DeployOptions
from mercury.deploy.safety import assert_deployment_target, planned_import_commands
from mercury.restore.phase3b_restorecheck_retire import proposed_drop_sql
from mercury.sync.isolation import inspect_restored_schema_isolation

INJECT = "erebus_threat_intel_prod`; DROP DATABASE `android_permission_intel"


def _boom(*_args, **_kwargs):
    raise AssertionError("query must not run for an unsafe identifier")


def test_quote_ident_rejects_backtick_and_quotes() -> None:
    assert quote_ident("android_permission_intel_dev") == "`android_permission_intel_dev`"
    with pytest.raises(ValueError, match="Unsafe SQL"):
        quote_ident(INJECT)
    with pytest.raises(ValueError, match="Unsafe SQL"):
        sql_schema_literal("dev'; DROP DATABASE foo; --")


def test_dump_and_backup_refuse_identifier_injection() -> None:
    with pytest.raises(ValueError, match="Unsafe SQL dump database"):
        build_dump_argv(INJECT, BACKUP_KIND_FULL)
    with pytest.raises(ValueError, match="Unsafe SQL dump database"):
        build_planned_dump_command(INJECT, BACKUP_KIND_FULL)
    with pytest.raises(BackupExecutionError, match="Unsafe SQL backup source"):
        assert_safe_backup_source(INJECT)


def test_deploy_refuses_identifier_injection() -> None:
    with pytest.raises(BackupExecutionError, match="Unsafe SQL deployment target"):
        assert_deployment_target(INJECT)
    with pytest.raises(BackupExecutionError, match="Unsafe SQL deployment target"):
        planned_import_commands(
            target_database=INJECT,
            dump_path="/tmp/a.sql.gz",
            options=DeployOptions(),
            exists_on_server=False,
        )


def test_isolation_and_inspect_refuse_identifier_injection() -> None:
    with pytest.raises(ValueError, match="Unsafe SQL isolation target"):
        inspect_restored_schema_isolation("dev'; DROP DATABASE foo; --", query_fn=_boom)
    result = inspect_database_on_server(
        INJECT,
        MariaDbConnectionConfig(host="localhost", user="root"),
        row_fn=_boom,
    )
    assert result.error is not None
    assert "Unsafe SQL inspect schema" in result.error


def test_phase3b_drop_sql_refuses_identifier_injection() -> None:
    with pytest.raises(ValueError, match="Unsafe SQL"):
        proposed_drop_sql(
            "_restorecheck_android_permission_intel`; DROP DATABASE `android_permission_intel",
            "_restorecheck_erebus_fixture_aaaa",
        )

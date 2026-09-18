"""Transport, credential, and read-only SQL hardening."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from mercury.backup.dump_planner import build_dump_argv
from mercury.backup.backup_runner import BackupExecutionError
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.client import (
    client_process_credentials,
    prepend_client_defaults,
    run_client_query,
)
from mercury.database.mariadb.config import (
    MariaDbConfigError,
    MariaDbConnectionConfig,
    assert_connection_tls,
    load_mariadb_config,
)
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.readonly_sql import assert_live_readonly_sql
from mercury.restore.restore_runner import execute_restore_into_database
from mercury.core.execution_policy import ExecutionPolicy


def test_remote_tcp_requires_tls() -> None:
    remote = MariaDbConnectionConfig(
        host="mariadb.example.com",
        user="mercury",
        password="secret",
        ssl_disabled=True,
    )
    with pytest.raises(MariaDbConfigError, match="requires TLS"):
        assert_connection_tls(remote)
    with pytest.raises(ValueError, match="requires TLS"):
        build_dump_argv(
            "erebus_threat_intel_prod",
            BACKUP_KIND_FULL,
            host="mariadb.example.com",
            ssl_disabled=True,
        )


def test_loopback_and_socket_may_skip_ssl() -> None:
    assert_connection_tls(
        MariaDbConnectionConfig(host="127.0.0.1", user="root", ssl_disabled=True)
    )
    assert_connection_tls(
        MariaDbConnectionConfig(
            host="remote.example",
            user="root",
            unix_socket="/var/lib/mysql/mysql.sock",
            ssl_disabled=True,
        )
    )
    argv = build_dump_argv("erebus_threat_intel_prod", BACKUP_KIND_FULL, host="localhost")
    assert "--skip-ssl" in argv


def test_password_env_name_must_be_a_safe_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local.toml"
    local.write_text(
        '[mariadb]\nhost="127.0.0.1"\nuser="u"\npassword_env="not a var"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("not a var", "secret")
    with pytest.raises(MariaDbConfigError, match="safe environment variable"):
        load_mariadb_config(local)


def test_client_credentials_use_private_defaults_file_not_mysql_pwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_PWD", "leaked")
    config = MariaDbConnectionConfig(host="127.0.0.1", user="u", password='s3c"ret')
    with client_process_credentials(config) as (env, extra):
        assert "MYSQL_PWD" not in env
        assert extra[0].startswith("--defaults-extra-file=")
        path = Path(extra[0].split("=", 1)[1])
        text = path.read_text(encoding="utf-8")
        assert 'password="s3c\\"ret"' in text
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        argv = prepend_client_defaults(["mariadb", "-u", "u"], extra)
        assert argv[1].startswith("--defaults-extra-file=")
        saved = path
    assert not saved.exists()


def test_live_readonly_sql_allows_show_create_and_rejects_writes() -> None:
    assert_live_readonly_sql("SHOW CREATE TABLE `erebus_threat_intel_prod`.`v`;")
    assert_live_readonly_sql("SHOW GRANTS FOR CURRENT_USER()")
    assert_live_readonly_sql("SELECT 1;\nSELECT VERSION();\nSHOW DATABASES;")
    with pytest.raises(MariaDbLiveError, match="write-capable"):
        assert_live_readonly_sql("SELECT 1 FROM t FOR UPDATE")
    with pytest.raises(MariaDbLiveError, match="begin with SHOW"):
        assert_live_readonly_sql("DROP DATABASE erebus_threat_intel_prod")
    with pytest.raises(MariaDbLiveError, match="begin with SHOW"):
        run_client_query(
            MariaDbConnectionConfig(host="127.0.0.1", user="u"),
            "DROP DATABASE erebus_threat_intel_prod",
            runner=lambda *_: (_ for _ in ()).throw(AssertionError("must not run")),
        )


def test_restore_refuses_symlink_dump(tmp_path: Path) -> None:
    target = tmp_path / "real.sql.gz"
    target.write_bytes(b"not-a-dump")
    link = tmp_path / "alias.sql.gz"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink not permitted")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path / "backups",
        allow_unsafe_backup_root=True,
    )
    with pytest.raises(BackupExecutionError, match="symlink dump path"):
        execute_restore_into_database(
            target_database="erebus_threat_intel_dev",
            dump_path=link,
            source_database="erebus_threat_intel_prod",
            execute=False,
            policy=policy,
        )

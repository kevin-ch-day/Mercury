"""Opt-in real MariaDB contract rehearsal on an isolated, no-network server.

MERCURY_CONTRACT_TEST_SOCKET must name a disposable server, never the host socket.
The fixture owns the entire server and creates production-named *synthetic* DBs.
"""

import gzip
import json
import os
from pathlib import Path

import pymysql
import pytest

from mercury.backup.checksum import sha256_file
from mercury.backup.content_contract import extract_restore_requirements
from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.mariadb.client import run_client_query
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.restore.account_contract import (
    DEV_SCOPES,
    PRINCIPAL,
    TEMP_RIGHTS,
    TEMP_SCOPE,
    assess_account,
)
from mercury.restore.restore_runner import execute_restore_into_database
from mercury.sync.restore_preflight import evaluate_restore_privileges


def test_isolated_server_restore_contract(tmp_path, monkeypatch):
    socket = os.environ.get("MERCURY_CONTRACT_TEST_SOCKET")
    if not socket:
        pytest.skip("explicit isolated MariaDB socket required")
    assert Path(socket).name == "mercury-contract-test.sock"
    assert Path(socket).resolve() != Path("/var/lib/mysql/mysql.sock")
    admin = pymysql.connect(unix_socket=socket, user="root", autocommit=True)

    def root(sql):
        with admin.cursor() as c:
            c.execute(sql)
            return c.fetchall()

    assert root("SELECT @@port")[0][0] == 0
    assert not root(
        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='erebus_threat_intel_prod'"
    )
    root("CREATE USER 'mercury_dev_restore'@'localhost'")
    user = "'mercury_dev_restore'@'localhost'"
    for name in DEV_SCOPES:
        root(f"GRANT ALL PRIVILEGES ON `{name}`.* TO {user}")
    old = TEMP_RIGHTS - {"CREATE ROUTINE", "ALTER ROUTINE"}
    root(f"GRANT {', '.join(sorted(old))} ON `{TEMP_SCOPE}`.* TO {user}")
    root("CREATE DATABASE android_permission_intel")
    root("CREATE TABLE android_permission_intel.tokens (id INT PRIMARY KEY)")
    root("INSERT INTO android_permission_intel.tokens VALUES (7)")
    root("CREATE DATABASE erebus_threat_intel_prod")
    cfg = MariaDbConnectionConfig(
        host="localhost",
        user="mercury_dev_restore",
        unix_socket=socket,
        use_client=True,
    )
    monkeypatch.setattr(
        "mercury.restore.restore_runner.load_mariadb_restore_config", lambda: cfg
    )
    source = "erebus_threat_intel_prod"
    target = "_restorecheck_contract_success"
    sql = """DROP TABLE IF EXISTS `t`;
CREATE TABLE `t` (id INT PRIMARY KEY) ENGINE=InnoDB;
LOCK TABLES `t` WRITE;
INSERT INTO `t` VALUES (1);
UNLOCK TABLES;
DROP PROCEDURE IF EXISTS `p`;
CREATE PROCEDURE `p`() SELECT 1;
DROP PROCEDURE `p`;
CREATE PROCEDURE `p`() SELECT 2;
CREATE TRIGGER `t_guard` BEFORE INSERT ON `t` FOR EACH ROW SET NEW.id = NEW.id;
CREATE VIEW `v_pi` AS SELECT id FROM `android_permission_intel`.`tokens`;
"""
    directory = tmp_path / "artifact"
    directory.mkdir()
    dump = directory / "fixture.sql.gz"
    with gzip.open(dump, "wt") as f:
        f.write(sql)
    manifest = {
        "backup_id": "synthetic-contract-fixture",
        "database": source,
        "dump_file": dump.name,
        "sha256": sha256_file(dump),
        "restore_requirements": extract_restore_requirements(
            dump, source_database=source
        ).model_dump(mode="json"),
        "row_counts": {"t": 1},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))

    def preflight(name=target):
        return evaluate_restore_privileges(
            source=source,
            target=name,
            backup_dir=directory,
            config=cfg,
            schema_rewrites={},
            receipt_root=tmp_path / "receipts",
        )

    before = preflight()
    assert before.missing_capabilities == [
        "ALTER ROUTINE",
        "CREATE ROUTINE",
        "SELECT on `android_permission_intel`.*",
    ]
    root(f"GRANT CREATE ROUTINE, ALTER ROUTINE ON `{TEMP_SCOPE}`.* TO {user}")
    assert preflight().missing_capabilities == [
        "SELECT on `android_permission_intel`.*"
    ]
    root(f"GRANT SELECT ON `android_permission_intel`.* TO {user}")
    assert preflight().passed
    grants = run_client_query(cfg, "SHOW GRANTS").splitlines()
    assert (
        assess_account(
            grants, current_user=PRINCIPAL, active_roles=[], pi_read_window=True
        )["status"]
        == "EXACT"
    )
    client = pymysql.connect(
        unix_socket=socket, user="mercury_dev_restore", autocommit=True
    )

    def denied(sql):
        with client.cursor() as c, pytest.raises(pymysql.MySQLError) as error:
            c.execute(sql)
        assert error.value.args[0] in {1044, 1142, 1227, 1370}

    for sql_denied in [
        "INSERT INTO android_permission_intel.tokens VALUES (8)",
        "UPDATE android_permission_intel.tokens SET id=8",
        "DELETE FROM android_permission_intel.tokens",
        "CREATE TABLE erebus_threat_intel_prod.blocked (id INT)",
        "CREATE PROCEDURE erebus_threat_intel_prod.blocked() SELECT 1",
        "DROP DATABASE erebus_threat_intel_prod",
        "CREATE DATABASE unauthorized_prod",
    ]:
        denied(sql_denied)
    root(f"GRANT UPDATE ON `android_permission_intel`.* TO {user}")
    assert not preflight().passed
    root(f"REVOKE UPDATE ON `android_permission_intel`.* FROM {user}")
    # Exact shadow rows must not invent the broad pattern's privileges.
    root(rf"GRANT ALTER ROUTINE ON `\_restorecheck\_shadow`.* TO {user}")
    shadow = preflight("_restorecheck_shadow")
    assert not shadow.passed and "CREATE" in shadow.missing_capabilities
    denied("CREATE DATABASE _restorecheck_shadow")
    root(rf"REVOKE ALTER ROUTINE ON `\_restorecheck\_shadow`.* FROM {user}")
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("[mercury]\ndry_run=false\nlive_actions_enabled=true\n")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=Path("/mnt/MERCURY_DATA_V2/mercury_backups"),
        config_path=policy_file,
    )
    # Use the real importer and live schema verification; inspect data before normal cleanup.
    from mercury.restore.restore_runner import _make_import_runner

    real_import = _make_import_runner(source, target)

    def checked_import(*args):
        real_import(*args)
        assert run_client_query(cfg, f"SELECT id FROM `{target}`.v_pi").strip() == "7"
        assert (
            run_client_query(cfg, f"SELECT COUNT(*) FROM `{target}`.t").strip() == "1"
        )
        assert (
            root(
                f"SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='{target}'"
            )[0][0]
            == 1
        )
        assert (
            root(
                f"SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='{target}'"
            )[0][0]
            == 1
        )

    result = execute_restore_into_database(
        target_database=target,
        source_database=source,
        dump_path=dump,
        execute=True,
        policy=policy,
        cleanup_after_success=True,
        receipt_root=tmp_path / "receipts",
        import_runner=checked_import,
    )
    assert result.executed and result.verification_passed and result.cleanup_dropped, (
        result
    )
    assert not root(
        f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{target}'"
    )
    # Real import succeeds, then a real verification failure preserves target and receipt.
    failed = "_restorecheck_contract_failed"
    manifest["row_counts"] = {"t": 1, "missing": 1}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    failed_result = execute_restore_into_database(
        target_database=failed,
        source_database=source,
        dump_path=dump,
        execute=True,
        policy=policy,
        cleanup_after_success=True,
        receipt_root=tmp_path / "receipts",
    )
    assert (
        failed_result.executed
        and failed_result.verification_passed is False
        and not failed_result.cleanup_dropped
    )
    assert root(
        f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{failed}'"
    )
    assert Path(failed_result.receipt_path).exists()
    root(f"REVOKE SELECT ON `android_permission_intel`.* FROM {user}")
    denied("SELECT * FROM android_permission_intel.tokens")
    evidence = os.environ.get("MERCURY_CONTRACT_TEST_EVIDENCE")
    if evidence:
        Path(evidence).write_text(
            json.dumps(
                {
                    "server": "isolated socket, port zero",
                    "old_preflight": before.model_dump(mode="json"),
                    "success": result.model_dump(mode="json"),
                    "preserved_failure": failed_result.model_dump(mode="json"),
                    "negative_privilege_checks": 7,
                    "pi_select_revoked": True,
                    "global_routine_grants_required": False,
                },
                indent=2,
            )
        )
    client.close()
    admin.close()

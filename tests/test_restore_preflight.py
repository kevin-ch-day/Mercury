"""Fail-closed preflight tests for ordinary prod→dev replacement."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from mercury.backup.checksum import sha256_file
from mercury.backup.content_contract import extract_restore_requirements
from mercury.sync.restore_preflight import evaluate_restore_privileges


SOURCE = "erebus_threat_intel_prod"
TARGET = "erebus_threat_intel_dev"


def _backup(tmp_path: Path, sql: str, *, mutate_contract=None) -> Path:
    directory = tmp_path / "backup"
    directory.mkdir()
    dump = directory / "erebus.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write(sql)
    contract = extract_restore_requirements(dump).model_dump(mode="json")
    if mutate_contract:
        mutate_contract(contract)
    manifest = {
        "backup_id": "erebus_threat_intel_prod-full-test",
        "database": SOURCE,
        "dump_file": dump.name,
        "sha256": sha256_file(dump),
        "restore_requirements": contract,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def _query(grants: str):
    def fake(_cfg, sql: str) -> str:
        if sql.startswith("SELECT VERSION"):
            return "11.8.8\tsystemadmin@localhost\tsystemadmin@localhost\tNONE"
        if "automatic_sp_privileges" in sql:
            return "1\t0\t0"
        if sql == "SHOW GRANTS":
            return grants
        raise AssertionError(sql)
    return fake


def _result(tmp_path: Path, sql: str, grants: str, **kwargs):
    return evaluate_restore_privileges(
        source=SOURCE,
        target=TARGET,
        backup_dir=_backup(tmp_path, sql, **kwargs),
        config=object(),
        query_fn=_query(grants),
        receipt_root=tmp_path / "receipts",
    )


def test_drop_procedure_requires_alter_routine_before_target_modification(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "/*!50003 DROP PROCEDURE IF EXISTS `p`; */\nCREATE PROCEDURE `p`() SELECT 1;\n",
        f"GRANT CREATE, DROP, SELECT, CREATE ROUTINE ON `{TARGET}`.* TO 'operator'@'localhost'",
    )
    assert result.passed is False
    assert result.missing_capabilities == ["ALTER ROUTINE"]
    assert result.target_untouched is True
    assert result.evidence_written is True
    assert Path(result.receipt_path or "").is_file()


def test_active_target_scoped_grants_satisfy_actual_requirements(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "DROP TABLE IF EXISTS `t`;\nCREATE TABLE `t` (id int);\nINSERT INTO `t` VALUES (1);\n",
        f"GRANT CREATE, DROP, SELECT, INSERT ON `{TARGET}`.* TO 'operator'@'localhost'",
    )
    assert result.passed is True
    assert result.missing_capabilities == []
    assert result.required_capabilities == ["CREATE", "DROP", "INSERT", "SELECT"]


def test_unknown_ddl_and_importer_contract_mismatch_fail_closed(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "CREATE SEQUENCE `x`;\n",
        f"GRANT ALL PRIVILEGES ON `{TARGET}`.* TO 'operator'@'localhost'",
        mutate_contract=lambda contract: contract.update({"import_transform_version": "old"}),
    )
    assert result.passed is False
    assert result.unknown_requirements
    assert any("incompatible" in item for item in result.inspection_issues)


def test_artifact_mutation_after_contract_blocks_preflight(tmp_path: Path) -> None:
    directory = _backup(tmp_path, "CREATE TABLE `t` (id int);\n")
    with gzip.open(directory / "erebus.sql.gz", "at", encoding="utf-8") as handle:
        handle.write("-- changed after manifest\n")
    result = evaluate_restore_privileges(
        source=SOURCE, target=TARGET, backup_dir=directory, config=object(),
        query_fn=_query(f"GRANT ALL PRIVILEGES ON `{TARGET}`.* TO 'operator'@'localhost'"),
        receipt_root=tmp_path / "receipts",
    )
    assert result.passed is False
    assert any("checksum" in issue for issue in result.inspection_issues)


def test_function_with_untrusted_binary_logging_is_not_guessed(tmp_path: Path) -> None:
    def binary_log_query(_cfg, sql: str) -> str:
        if sql.startswith("SELECT VERSION"):
            return "11.8.8\toperator@localhost\toperator@localhost\tNONE"
        if "automatic_sp_privileges" in sql:
            return "1\t1\t0"
        if sql == "SHOW GRANTS":
            return f"GRANT ALL PRIVILEGES ON `{TARGET}`.* TO 'operator'@'localhost'"
        raise AssertionError(sql)
    result = evaluate_restore_privileges(
        source=SOURCE, target=TARGET,
        backup_dir=_backup(tmp_path, "CREATE FUNCTION `f`() RETURNS INT RETURN 1;\n"),
        config=object(), query_fn=binary_log_query, receipt_root=tmp_path / "receipts",
    )
    assert result.passed is False
    assert any("binary-log" in item for item in result.unknown_requirements)

"""Regression tests for full logical-backup recoverability contracts."""

from __future__ import annotations

import gzip
from pathlib import Path

from mercury.backup.content_contract import (
    BackupObjectInventory,
    build_backup_content_contract,
    extract_dump_object_inventory,
    extract_restore_requirements,
    fetch_live_object_inventory,
)
from mercury.backup.dump_planner import build_dump_argv, build_planned_dump_command
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.config import MariaDbConnectionConfig


def test_full_dump_explicitly_includes_recoverability_object_flags() -> None:
    argv = build_dump_argv("erebus_threat_intel_prod", BACKUP_KIND_FULL)
    command = build_planned_dump_command("erebus_threat_intel_prod", BACKUP_KIND_FULL)
    for flag in ("--routines", "--triggers", "--events"):
        assert flag in argv
        assert flag in command


def test_restore_requirements_detects_versioned_routine_drop(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("-- DROP PROCEDURE is only a comment here\n")
        handle.write("/*!50003 DROP PROCEDURE IF EXISTS `p_demo` */;\n")
        handle.write("CREATE DEFINER=`root`@`localhost` PROCEDURE `p_demo`() SELECT 1;\n")
        handle.write("INSERT INTO notes VALUES ('DROP PROCEDURE');\n")

    contract = extract_restore_requirements(dump)

    assert contract.verified is True
    assert contract.artifact_file == "erebus.sql.gz"
    assert contract.statement_classes == ["CREATE PROCEDURE", "DROP PROCEDURE", "INSERT"]


def test_restore_requirements_fail_closed_for_unknown_top_level_ddl(tmp_path: Path) -> None:
    dump = tmp_path / "unknown.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("CREATE SEQUENCE `future_sequence`;\n")
        handle.write("INSERT INTO notes VALUES ('CREATE SEQUENCE');\n")

    contract = extract_restore_requirements(dump)

    assert contract.statement_classes == ["INSERT"]
    assert contract.unknown_privileged_statements == ["CREATE SEQUENCE `future_sequence`;"]


def test_restore_requirements_detects_all_object_statement_families(tmp_path: Path) -> None:
    dump = tmp_path / "objects.sql.gz"
    lines = [
        "CREATE OR REPLACE VIEW `v` AS SELECT 1;",
        "DROP TRIGGER IF EXISTS `tr`;",
        "CREATE TRIGGER `tr` BEFORE INSERT ON `t` FOR EACH ROW SET @x = 1;",
        "CREATE FUNCTION `f`() RETURNS INT RETURN 1;",
        "ALTER FUNCTION `f` COMMENT 'x';",
        "CREATE EVENT `e` ON SCHEDULE EVERY 1 DAY DO SELECT 1;",
        "DROP EVENT IF EXISTS `e`;",
        "LOCK TABLES `t` WRITE;",
        "UNLOCK TABLES;",
    ]
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    contract = extract_restore_requirements(dump)

    assert contract.statement_classes == [
        "ALTER FUNCTION", "CREATE EVENT", "CREATE FUNCTION", "CREATE TRIGGER",
        "CREATE VIEW", "DROP EVENT", "DROP TRIGGER", "LOCK TABLES", "UNLOCK TABLES",
    ]


def test_restore_requirements_recognizes_mariadb_view_prefixes(tmp_path: Path) -> None:
    dump = tmp_path / "view.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write(
            "/*!50001 CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`localhost` "
            "SQL SECURITY DEFINER VIEW `v` AS SELECT 1 */;\n"
        )

    contract = extract_restore_requirements(dump)

    assert contract.statement_classes == ["CREATE VIEW"]
    assert contract.unknown_privileged_statements == []


def test_restore_requirements_recognizes_split_mariadb_view_prefixes(tmp_path: Path) -> None:
    """MariaDB dumps final view DDL across separate executable comments."""
    dump = tmp_path / "split-view.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("/*!50001 CREATE ALGORITHM=UNDEFINED */;\n")
        handle.write("/*!50013 DEFINER=`root`@`localhost` SQL SECURITY DEFINER */;\n")
        handle.write("/*!50001 VIEW `v` AS SELECT 1 */;\n")

    contract = extract_restore_requirements(dump)

    assert contract.statement_classes == ["CREATE VIEW"]
    assert contract.unknown_privileged_statements == []


def test_restore_requirements_records_external_schema_dependencies(tmp_path: Path) -> None:
    dump = tmp_path / "erebus_threat_intel_prod_20260813_221746_435.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write(
            "CREATE VIEW `permission_unknown_metrics` AS SELECT * FROM "
            "`android_permission_intel`.`android_permission_dict_unknown`;\n"
        )
        handle.write("INSERT INTO note VALUES ('android_permission_intel.not_an_identifier');\n")

    contract = extract_restore_requirements(dump)

    assert contract.external_schema_references == ["android_permission_intel"]
    assert [item.object_name for item in contract.external_schema_objects] == [
        "permission_unknown_metrics"
    ]
    assert contract.external_schema_objects[0].object_kind == "view"


def test_restore_requirements_rejects_unfinished_mariadb_view_prefix(tmp_path: Path) -> None:
    dump = tmp_path / "unfinished-view.sql.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("/*!50001 CREATE ALGORITHM=UNDEFINED */;\n")

    contract = extract_restore_requirements(dump)

    assert contract.unknown_privileged_statements == ["CREATE ALGORITHM=UNDEFINED"]


def test_dump_inventory_parses_all_recovery_object_classes(tmp_path: Path) -> None:
    dump = tmp_path / "fixture.sql.gz"
    dump.write_bytes(
        gzip.compress(
            b"\n".join(
                [
                    b"CREATE TABLE `sample` (`id` int);",
                    b"/*!50001 CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`localhost` SQL SECURITY DEFINER VIEW `sample_view` AS select 1 */;",
                    b"/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `sample_trigger` BEFORE INSERT ON `sample` FOR EACH ROW SET NEW.id = 1 */;;",
                    b"CREATE DEFINER=`root`@`localhost` PROCEDURE `sample_procedure`() SELECT 1;;",
                    b"CREATE DEFINER=`root`@`localhost` FUNCTION `sample_function`() RETURNS INT RETURN 1;;",
                    b"CREATE DEFINER=`root`@`localhost` EVENT `sample_event` ON SCHEDULE EVERY 1 DAY DO SELECT 1;;",
                    b"-- An application comment may say CREATE PROCEDURE address without declaring one.",
                ]
            )
        )
    )

    inventory = extract_dump_object_inventory(dump)

    assert inventory.model_dump() == {
        "tables": ["sample"],
        "views": ["sample_view"],
        "triggers": ["sample_trigger"],
        "procedures": ["sample_procedure"],
        "functions": ["sample_function"],
        "events": ["sample_event"],
    }


def test_content_contract_fails_closed_for_missing_live_routine() -> None:
    live = BackupObjectInventory(tables=["sample"], procedures=["required_procedure"])
    dumped = BackupObjectInventory(tables=["sample"])

    contract = build_backup_content_contract(live, dumped)

    assert contract.verified is False
    assert contract.issues == ["procedures: dump missing required_procedure"]


def test_dump_inventory_does_not_treat_review_trigger_as_view(tmp_path: Path) -> None:
    """Object-type recognition must use SQL tokens, not name substrings."""
    dump = tmp_path / "review-trigger.sql.gz"
    dump.write_bytes(
        gzip.compress(
            b"/*!50003 CREATE*/ /*!50017 DEFINER=`app`@`localhost`*/ "
            b"/*!50003 TRIGGER bi_android_permission_review_state_guard "
            b"BEFORE INSERT ON android_permission_review_state FOR EACH ROW SET @x = 1;\n"
        )
    )

    inventory = extract_dump_object_inventory(dump)

    assert inventory.triggers == ["bi_android_permission_review_state_guard"]
    assert inventory.views == []


def test_live_inventory_queries_each_recovery_object_class() -> None:
    statements: list[str] = []
    config = MariaDbConnectionConfig(host="localhost", user="root")

    def fake_scalars(_config, sql: str) -> list[str]:
        statements.append(sql)
        if "ROUTINES" in sql:
            return ["stored_procedure"]
        return []

    inventory = fetch_live_object_inventory(
        config,
        "erebus_threat_intel_prod",
        scalars=fake_scalars,
    )

    assert inventory.procedures == ["stored_procedure"]
    assert len(statements) == 6
    assert any("TABLE_TYPE = 'BASE TABLE'" in sql for sql in statements)
    assert any("TABLE_TYPE = 'VIEW'" in sql for sql in statements)
    assert any("information_schema.TRIGGERS" in sql for sql in statements)
    assert any("information_schema.ROUTINES" in sql for sql in statements)
    assert any("information_schema.EVENTS" in sql for sql in statements)

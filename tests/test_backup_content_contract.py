"""Regression tests for full logical-backup recoverability contracts."""

from __future__ import annotations

import gzip
from pathlib import Path

from mercury.backup.content_contract import (
    BackupObjectInventory,
    build_backup_content_contract,
    extract_dump_object_inventory,
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

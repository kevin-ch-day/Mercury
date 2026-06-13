"""Tests for targeted SQL import stream rewriting."""

from __future__ import annotations

import gzip
from pathlib import Path

from mercury.database.mariadb.import_stream import run_compressed_sql_import


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_import_stream_strips_source_database_directives_and_definers(tmp_path: Path) -> None:
    dump_path = tmp_path / "sample.sql.gz"
    payload = "\n".join(
        [
            "CREATE DATABASE `erebus_threat_intel_prod`;",
            "USE `erebus_threat_intel_prod`;",
            "CREATE DEFINER=`root`@`localhost` VIEW `v_demo` AS SELECT 1;",
            "CREATE TABLE `demo` (`id` int);",
            "",
        ]
    )
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(payload)

    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(
        fake_client,
        f"""#!/usr/bin/env bash
cat > "{capture}"
exit 0
""",
    )

    run_compressed_sql_import(
        [str(fake_client)],
        {},
        dump_path,
    )

    written = capture.read_text(encoding="utf-8")
    assert "CREATE DATABASE" not in written
    assert "USE `erebus_threat_intel_prod`" not in written
    assert "SQL SECURITY DEFINER" not in written
    assert "DEFINER=" not in written
    assert "CREATE TABLE `demo`" in written


def test_import_stream_rewrites_source_database_to_target(tmp_path: Path) -> None:
    dump_path = tmp_path / "trigger.sql.gz"
    payload = (
        "CREATE TABLE `erebus_threat_intel_prod`.`demo` (`id` int);\n"
        "/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ "
        "/*!50003 TRIGGER erebus_threat_intel_prod.trg_sample "
        "BEFORE INSERT ON erebus_threat_intel_prod.demo FOR EACH ROW BEGIN END */;\n"
    )
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(payload)

    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(
        fake_client,
        f"""#!/usr/bin/env bash
cat > "{capture}"
exit 0
""",
    )

    run_compressed_sql_import(
        [str(fake_client)],
        {},
        dump_path,
        rewrite_database=("erebus_threat_intel_prod", "erebus_threat_intel_dev"),
    )

    written = capture.read_text(encoding="utf-8")
    assert "erebus_threat_intel_prod" not in written
    assert "erebus_threat_intel_dev" in written
    assert "TRIGGER erebus_threat_intel_dev.trg_sample" in written


def test_import_stream_strips_conditional_definer_comments_from_triggers(tmp_path: Path) -> None:
    dump_path = tmp_path / "trigger.sql.gz"
    payload = (
        "/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ "
        "/*!50003 TRIGGER demo.trg_sample BEFORE INSERT ON demo FOR EACH ROW BEGIN END */;\n"
    )
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(payload)

    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(
        fake_client,
        f"""#!/usr/bin/env bash
cat > "{capture}"
exit 0
""",
    )

    run_compressed_sql_import([str(fake_client)], {}, dump_path)

    written = capture.read_text(encoding="utf-8")
    assert "DEFINER=" not in written
    assert "**" not in written
    assert "TRIGGER demo.trg_sample" in written


def test_import_stream_closes_stdin_when_client_exits_early(tmp_path: Path) -> None:
    dump_path = tmp_path / "sample.sql.gz"
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `demo` (`id` int);\n" * 100)

    fake_client = tmp_path / "exit-early.sh"
    _write_executable(
        fake_client,
        """#!/usr/bin/env bash
read -r _ || true
exit 0
""",
    )

    run_compressed_sql_import([str(fake_client)], {}, dump_path)


"""SQL recovery rewrites must preserve data and reject ambiguous input."""

from __future__ import annotations

import gzip
import sys

import pytest

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.mariadb.import_stream import run_compressed_sql_import
from mercury.database.mariadb.sql_transform import SqlTransformer


def transform(sql, *, rewrites=None):
    processor = SqlTransformer(strip_definer=True, strip_database_directives=True,
        rewrites=rewrites or {})
    output = "".join(processor.transform(line) for line in sql.splitlines(keepends=True))
    processor.finish()
    return output


@pytest.mark.parametrize("literal", [
    "'DEFINER=`literal`@`data` preserved'",
    '"DEFINER=`literal`@`data` preserved"',
    "'escaped \\' DEFINER=`literal`@`data` preserved'",
    "'doubled '' DEFINER=`literal`@`data` preserved'",
    "'/*!50017 DEFINER=`literal`@`data`*/'",
])
def test_definer_removal_preserves_routine_string_literals(literal):
    sql = f"CREATE DEFINER=`owner`@`localhost` PROCEDURE p() SQL SECURITY DEFINER SELECT {literal};\n"
    output = transform(sql)
    assert literal in output
    assert "DEFINER=`owner`" not in output
    assert "SQL SECURITY DEFINER" in output


def test_comment_and_multiline_literal_state_is_preserved():
    sql = (
        "-- DEFINER=`comment`@`host` source.t\n"
        "/* multi-line ordinary comment\n"
        "CREATE DEFINER=`comment`@`host` VIEW v AS SELECT * FROM source.t;\n"
        "USE `source`;\n*/\n"
        "CREATE DEFINER=`owner`@`host` PROCEDURE p()\n"
        "BEGIN\n  SELECT 'multi-line string\n"
        "USE `source`;\nCREATE DEFINER=`literal`@`data` VIEW v;\n"
        "source.t\n';\n"
        "  SELECT * FROM `source` . `t`;\nEND;\n"
    )
    expected = sql.replace("DEFINER=`owner`@`host`", " ").replace("`source` . `t`", "`target` . `t`")
    output = transform(sql, rewrites={"source": "target"})
    # Only the account clause and schema-qualified code identifier may change.
    assert output == expected


@pytest.mark.parametrize("wrapper", ["/*!50017", "/*M!100100"])
def test_executable_comments_are_rewritten_but_ordinary_comments_remain(wrapper):
    sql = (
        f"/*!50003 CREATE*/ {wrapper} DEFINER=`root`@`localhost`*/ "
        "/*!50003 TRIGGER source.trg BEFORE INSERT ON source.t FOR EACH ROW "
        "SET NEW.note = 'DEFINER=`literal`@`host` source.t' */;\n"
        "# DEFINER=`comment`@`host` source.t\n"
    )
    output = transform(sql, rewrites={"source": "target"})
    assert "TRIGGER target.trg" in output and "ON target.t" in output
    assert "DEFINER=`root`" not in output
    assert "'DEFINER=`literal`@`host` source.t'" in output
    assert "# DEFINER=`comment`@`host` source.t\n" in output
    assert wrapper in output


def test_non_clause_definer_identifiers_and_literals_are_unchanged():
    sql = "UPDATE t SET definer='DEFINER=`literal`@`host`';\nSELECT `DEFINER`, 3--2;\n"
    assert transform(sql) == sql


def test_multiline_versioned_view_header_preserves_sql_security_definer():
    sql = (
        "/*!50001 CREATE ALGORITHM=UNDEFINED */\n"
        "/*!50013 DEFINER=`root`@`localhost` SQL SECURITY DEFINER */\n"
        "/*!50001 VIEW `v` AS SELECT 1 */;\n"
    )
    output = transform(sql)
    assert "DEFINER=`root`" not in output
    assert "SQL SECURITY DEFINER" in output


def test_schema_rewrites_do_not_cascade_into_other_rewrites():
    sql = "SELECT * FROM source.t JOIN target.t USING(id);\n"
    output = transform(sql, rewrites={"source": "target", "target": "third"})
    assert output == "SELECT * FROM target.t JOIN third.t USING(id);\n"


@pytest.mark.parametrize("sql", [
    "CREATE DEFINER=\n`user`@`host` PROCEDURE p() SELECT 1;\n",
    "CREATE DEFINER=unquoted@host PROCEDURE p() SELECT 1;\n",
    "CREATE DEFINER=`user`/* comment */@`host` PROCEDURE p() SELECT 1;\n",
    "CREATE DEFINER='user'@'unfinished\n",
    "SET SQL_MODE='NO_BACKSLASH_ESCAPES';\n",
    "SET SQL_MODE=\n'ANSI_QUOTES';\n",
    "SET SQL_MODE=CONCAT('ANSI','_QUOTES');\n",
    "CREATE DEFINER=CURRENT_USER(1) PROCEDURE p() SELECT 1;\n",
    "SET @mode='ANSI_QUOTES';\nSET SQL_MODE=@mode;\nCREATE VIEW v AS SELECT 1;\n",
    "/*!50003 SET sql_mode='ANSI_QUOTES' */;\n",
    "CREATE DATABASE\n`source`;\n",
    "CREATE\nDATABASE `source`;\n",
    "USE `source`; SELECT 1;\n",
    "SELECT 'unterminated\n",
    "/* unterminated comment\n",
])
def test_unsupported_sql_is_rejected(sql):
    with pytest.raises(ValueError):
        transform(sql)


def test_stream_preserves_arbitrary_literal_bytes_and_multiline_strings(tmp_path):
    payload = (
        b"CREATE DEFINER=`owner`@`host` PROCEDURE p() SELECT 'literal\n"
        b"USE `source`;\nDEFINER=`literal`@`data`\n\xff\x80';\n"
        b"INSERT INTO `t` VALUES (1,'source.t DEFINER=`data`@`host` \xfe');\n"
    )
    dump = tmp_path / "dump.sql.gz"
    dump.write_bytes(gzip.compress(payload))
    capture = tmp_path / "capture.sql"
    run_compressed_sql_import(
        [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())", str(capture)],
        {}, dump, session_preamble=False, rewrite_database=("source", "target"),
    )
    assert capture.read_bytes() == payload.replace(b"DEFINER=`owner`@`host`", b" ")


def test_rejected_transform_reaps_the_import_client(tmp_path, monkeypatch):
    import subprocess
    from mercury.database.mariadb import import_stream

    dump = tmp_path / "bad.sql.gz"
    dump.write_bytes(gzip.compress(b"SET SQL_MODE='ANSI_QUOTES';\n"))
    processes = []
    real_popen = subprocess.Popen
    def capture_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(import_stream.subprocess, "Popen", capture_process)
    monkeypatch.setattr(import_stream.shutil, "which", lambda _: None)
    with pytest.raises(BackupExecutionError, match="quote semantics"):
        run_compressed_sql_import([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], {}, dump)
    assert processes and all(process.poll() is not None for process in processes)


def test_oversized_sql_lines_are_refused_before_lexer_expansion(tmp_path, monkeypatch):
    from mercury.database.mariadb import import_stream

    dump = tmp_path / "oversized.sql.gz"
    dump.write_bytes(gzip.compress(b"INSERT INTO t VALUES ('" + b"a" * 256 + b"');\n"))
    monkeypatch.setattr(import_stream, "MAX_SQL_LINE_BYTES", 128)
    with pytest.raises(ValueError, match="physical line exceeds"):
        import_stream.validate_sql_transform(dump, source="source", target="target")


def test_known_saved_sql_modes_remain_supported():
    sql = (
        "SET @saved_sql_mode=@@sql_mode;\n"
        "SET sql_mode='STRICT_TRANS_TABLES';\n"
        "SET sql_mode=@saved_sql_mode;\n"
        "CREATE DEFINER=`root`@`localhost` VIEW v AS SELECT 1;\n"
    )
    assert "DEFINER=`root`" not in transform(sql)


def test_database_ddl_in_routine_body_is_rejected_instead_of_deleted():
    sql = (
        "DELIMITER ;;\nCREATE PROCEDURE p()\nBEGIN\n"
        "CREATE DATABASE other_prod;\nEND;;\nDELIMITER ;\n"
    )
    with pytest.raises(ValueError, match="inside routine bodies"):
        transform(sql)


def test_routine_runtime_mode_assignment_is_not_executed_during_import():
    sql = (
        "DELIMITER ;;\nCREATE DEFINER=`root`@`localhost` PROCEDURE p()\nBEGIN\n"
        "SET SQL_MODE='ANSI_QUOTES';\nSELECT 'preserved';\nEND;;\nDELIMITER ;\n"
    )
    assert transform(sql) == sql.replace("DEFINER=`root`@`localhost`", " ")


def test_create_or_replace_database_directive_is_removed():
    sql = "CREATE OR REPLACE DATABASE other_prod;\nCREATE TABLE t(id INT);\n"
    assert transform(sql) == "CREATE TABLE t(id INT);\n"

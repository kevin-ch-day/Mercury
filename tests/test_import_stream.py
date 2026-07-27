"""Tests for targeted SQL import stream rewriting."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

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
    assert "SET SESSION unique_checks=0" in written
    assert "SET SESSION foreign_key_checks=0" in written


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


def test_import_stream_rewrites_package_bound_cross_schema_identifiers(tmp_path: Path) -> None:
    dump_path = tmp_path / "cross_schema.sql.gz"
    payload = "\n".join(
        [
            "CREATE VIEW `v_dependency` AS SELECT * FROM `android_permission_intel`.`android_permission_dict_unknown`;",
            "CREATE PROCEDURE `p_dependency`() SELECT * FROM android_permission_intel.android_permission_dict_unknown;",
            "CREATE TRIGGER `t_dependency` BEFORE INSERT ON `erebus_threat_intel_prod`.`queue` FOR EACH ROW SET @x = 1;",
            "INSERT INTO note VALUES ('android_permission_intel.not_an_identifier');",
            "",
        ]
    )
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(payload)
    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(fake_client, f'#!/usr/bin/env bash\ncat > "{capture}"\n')

    run_compressed_sql_import(
        [str(fake_client)],
        {},
        dump_path,
        rewrite_databases={
            "erebus_threat_intel_prod": "_restorecheck_erebus",
            "android_permission_intel": "_restorecheck_android",
        },
    )

    written = capture.read_text(encoding="utf-8")
    assert "`_restorecheck_android`.`android_permission_dict_unknown`" in written
    assert "_restorecheck_android.android_permission_dict_unknown" in written
    assert "`_restorecheck_erebus`.`queue`" in written
    assert "'android_permission_intel.not_an_identifier'" in written


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


def test_import_stream_skips_rewrite_on_plain_insert_rows(tmp_path: Path) -> None:
    """Bulk INSERT rows must not pay schema-rewrite cost (Scytale hot path)."""
    dump_path = tmp_path / "bulk.sql.gz"
    # String payload deliberately contains the source DB name; rewrite must not
    # touch it when the INSERT head is not schema-qualified.
    payload = (
        "INSERT INTO `static_string_samples` VALUES "
        "(1,'scytaledroid_core_prod.looks_like_schema');\n"
        "INSERT INTO `scytaledroid_core_prod`.`other` VALUES (2);\n"
    )
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(payload)
    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(fake_client, f'#!/usr/bin/env bash\ncat > "{capture}"\n')

    run_compressed_sql_import(
        [str(fake_client)],
        {},
        dump_path,
        rewrite_database=(
            "scytaledroid_core_prod",
            "_restorecheck_scytaledroid_core_prod_x",
        ),
    )
    written = capture.read_text(encoding="utf-8")
    assert (
        "INSERT INTO `static_string_samples` VALUES "
        "(1,'scytaledroid_core_prod.looks_like_schema');"
    ) in written
    assert "`_restorecheck_scytaledroid_core_prod_x`.`other`" in written


def test_import_stream_reports_progress(tmp_path: Path) -> None:
    dump_path = tmp_path / "progress.sql.gz"
    # Force multiple progress ticks with a tiny threshold.
    line = "INSERT INTO `demo` VALUES (1);\n"
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(line * 2000)
    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(fake_client, f'#!/usr/bin/env bash\ncat > "{capture}"\n')
    ticks: list[tuple[int, int, float]] = []

    run_compressed_sql_import(
        [str(fake_client)],
        {},
        dump_path,
        on_progress=lambda *args: ticks.append(args),
        progress_every_bytes=64,
    )
    assert ticks
    assert all(elapsed >= 0 for _, _, elapsed in ticks)


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


def test_import_stream_passthrough_multiline_insert_values(tmp_path: Path) -> None:
    """MariaDB dumps put one VALUES row per line — continuations must stay hot-path."""
    dump_path = tmp_path / "multiline.sql.gz"
    # Opening INSERT does not end with ';'; value rows follow.
    payload = (
        "INSERT INTO `static_string_samples` VALUES\n"
        "(1,'scytaledroid_core_prod.looks_like_schema'),\n"
        "(2,'another'),\n"
        "(3,'done');\n"
        "INSERT INTO `scytaledroid_core_prod`.`other` VALUES\n"
        "(9);\n"
    )
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write(payload)
    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(fake_client, f'#!/usr/bin/env bash\ncat > "{capture}"\n')

    run_compressed_sql_import(
        [str(fake_client)],
        {},
        dump_path,
        rewrite_database=(
            "scytaledroid_core_prod",
            "_restorecheck_scytaledroid_core_prod_x",
        ),
    )
    written = capture.read_text(encoding="utf-8")
    assert "(1,'scytaledroid_core_prod.looks_like_schema')," in written
    assert "(3,'done');" in written
    assert "INSERT INTO `_restorecheck_scytaledroid_core_prod_x`.`other` VALUES" in written
    assert "(9);" in written


def test_import_stream_can_disable_session_preamble(tmp_path: Path) -> None:
    dump_path = tmp_path / "nopreamble.sql.gz"
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `demo` (`id` int);\n")
    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(fake_client, f'#!/usr/bin/env bash\ncat > "{capture}"\n')

    run_compressed_sql_import(
        [str(fake_client)],
        {},
        dump_path,
        session_preamble=False,
    )
    written = capture.read_text(encoding="utf-8")
    assert "SET SESSION unique_checks" not in written
    assert "CREATE TABLE `demo`" in written


def test_import_stream_uses_pigz_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump_path = tmp_path / "via_pigz.sql.gz"
    with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `demo` (`id` int);\n")
    capture = tmp_path / "captured.sql"
    fake_client = tmp_path / "fake-mariadb.sh"
    _write_executable(fake_client, f'#!/usr/bin/env bash\ncat > "{capture}"\n')

    pigz = tmp_path / "pigz"
    _write_executable(pigz, "#!/usr/bin/env bash\nexec gzip -dc \"$@\"\n")
    monkeypatch.setattr(
        "mercury.database.mariadb.import_stream.shutil.which",
        lambda name: str(pigz) if name == "pigz" else None,
    )

    run_compressed_sql_import([str(fake_client)], {}, dump_path)
    written = capture.read_text(encoding="utf-8")
    assert "CREATE TABLE `demo`" in written

"""Dumpability preflight and mariadb-dump error classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.backup.dump_preflight import (
    DatabaseDumpability,
    classify_dump_tool_error,
    describe_view_dump_failure,
    parse_force_errors_by_line,
    parse_force_show_create_errors,
    resolve_force_show_create_errors,
    try_assess_sources_dumpability,
    view_dumpability_error,
    view_dumpability_issues,
)
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.errors import MariaDbLiveError


DUMP_STDERR = (
    "mariadb-dump: Couldn't execute 'show create table `v_masvs_matrix`': "
    "Illegal mix of collations (utf8mb4_uca1400_ai_ci,COERCIBLE) and "
    "(utf8mb4_general_ci,COERCIBLE) for operation '=' (1267)"
)


def test_classify_collation_mix_on_show_create() -> None:
    text = classify_dump_tool_error(DUMP_STDERR)
    assert "view `v_masvs_matrix` is not dumpable" in text
    assert "utf8mb4_uca1400_ai_ci vs utf8mb4_general_ci" in text
    assert "will not skip views" in text


def test_classify_names_owning_project() -> None:
    text = classify_dump_tool_error(DUMP_STDERR, database="scytaledroid_core_prod")
    assert "ScytaleDroid" in text


def test_classify_preserves_unrelated_dump_errors() -> None:
    raw = "mysqldump: Got error: 1045: Access denied for user"
    assert classify_dump_tool_error(raw) == raw


def test_describe_view_failure_without_collation() -> None:
    text = describe_view_dump_failure("v_other", "ERROR 1142 (42000): SHOW command denied")
    assert text.startswith("view `v_other` is not dumpable:")
    assert "SHOW command denied" in text


def test_view_dumpability_error_lists_failing_views() -> None:
    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")

    def fake_show(_config, sql: str) -> None:
        if "v_masvs_matrix" in sql:
            raise MariaDbLiveError(DUMP_STDERR)
        if "v_broken" in sql:
            raise MariaDbLiveError("ERROR 1142 (42000): SHOW command denied")

    issue = view_dumpability_error(
        config,
        "scytaledroid_core_prod",
        ["vw_ok", "v_masvs_matrix", "v_broken"],
        show_create=fake_show,
    )
    assert issue is not None
    assert "2 views are not dumpable" in issue
    assert "`v_masvs_matrix`" in issue
    assert "`v_broken`" in issue
    assert "will not skip views" in issue
    assert "ScytaleDroid" in issue


def test_view_dumpability_issues_skips_healthy_views() -> None:
    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")
    shown: list[str] = []

    def fake_show(_config, sql: str) -> None:
        shown.append(sql)

    issues = view_dumpability_issues(
        config,
        "erebus_threat_intel_prod",
        ["v_ok"],
        show_create=fake_show,
    )
    assert issues == []
    assert shown == ["SHOW CREATE TABLE `erebus_threat_intel_prod`.`v_ok`"]


def test_default_show_create_uses_mariadb_cli_not_pymysql() -> None:
    from mercury.backup import dump_preflight as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "run_client_query" in source
    assert "from mercury.database.mariadb.session import readonly_row" not in source


def test_empty_view_list_is_dumpable() -> None:
    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")
    assert view_dumpability_error(config, "erebus_threat_intel_prod", []) is None


FORCE_STDERR = """--------------
SHOW CREATE TABLE `scytaledroid_core_prod`.`v_masvs_matrix`
--------------

ERROR 1267 (HY000) at line 14: Illegal mix of collations (utf8mb4_uca1400_ai_ci,COERCIBLE) and (utf8mb4_general_ci,COERCIBLE) for operation '='
"""


def test_parse_force_show_create_errors() -> None:
    parsed = parse_force_show_create_errors(FORCE_STDERR)
    assert "v_masvs_matrix" in parsed
    assert "Illegal mix of collations" in parsed["v_masvs_matrix"]


def test_parse_force_errors_by_line_without_sql_echo() -> None:
    stderr = (
        "ERROR 1267 (HY000) at line 2: Illegal mix of collations "
        "(utf8mb4_uca1400_ai_ci,COERCIBLE) and (utf8mb4_general_ci,COERCIBLE) "
        "for operation '='\n"
    )
    parsed = parse_force_errors_by_line(stderr)
    assert parsed[2].startswith("Illegal mix of collations")
    mapped = resolve_force_show_create_errors(
        stderr, ["v_static_masvs_matrix_v1", "v_masvs_matrix"]
    )
    assert mapped == {
        "v_masvs_matrix": parsed[2],
    }


def test_batch_maps_line_only_force_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")

    def fake_force(_config, _sql: str) -> tuple[int, str, str]:
        return (
            0,
            "",
            "ERROR 1267 (HY000) at line 2: Illegal mix of collations "
            "(utf8mb4_uca1400_ai_ci,COERCIBLE) and (utf8mb4_general_ci,COERCIBLE) "
            "for operation '='\n",
        )

    monkeypatch.setattr(
        "mercury.backup.dump_preflight._client_force_script", fake_force
    )
    issues = view_dumpability_issues(
        config,
        "scytaledroid_core_prod",
        ["v_ok", "v_masvs_matrix"],
    )
    assert len(issues) == 1
    assert "`v_masvs_matrix`" in issues[0]
    assert "utf8mb4_uca1400_ai_ci vs utf8mb4_general_ci" in issues[0]


def test_unparsed_force_error_falls_back_to_per_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")

    def fake_force(_config, _sql: str) -> tuple[int, str, str]:
        # --force can exit 0 even when a statement failed.
        return 0, "", "ERROR 1267 (HY000): Illegal mix of collations\n"

    shown: list[str] = []

    def fake_show(_config, sql: str) -> None:
        shown.append(sql)
        if "v_masvs_matrix" in sql:
            raise MariaDbLiveError(DUMP_STDERR)

    monkeypatch.setattr(
        "mercury.backup.dump_preflight._client_force_script", fake_force
    )
    monkeypatch.setattr(
        "mercury.backup.dump_preflight._default_show_create", fake_show
    )
    issues = view_dumpability_issues(
        config,
        "scytaledroid_core_prod",
        ["v_ok", "v_masvs_matrix"],
    )
    assert shown == [
        "SHOW CREATE TABLE `scytaledroid_core_prod`.`v_ok`",
        "SHOW CREATE TABLE `scytaledroid_core_prod`.`v_masvs_matrix`",
    ]
    assert any("`v_masvs_matrix`" in item for item in issues)


def test_inspect_error_is_blocked_not_ok(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.backup.terminal.dumpability import print_dumpability_report
    from mercury.backup.dump_preflight import SourcesDumpability

    report = SourcesDumpability(
        entries=[
            DatabaseDumpability(
                database="erebus_threat_intel_prod",
                error="MariaDB CLI dumpability probe timed out after 60s",
            )
        ]
    )
    assert report.blocked_names() == {"erebus_threat_intel_prod"}
    assert "inspect failed" in report.issue_lines()[0]
    print_dumpability_report(report)
    out = capsys.readouterr().out
    assert "Dumpability OK" not in out
    assert "inspect failed" in out


def test_try_assess_sources_reports_blocked_database() -> None:
    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")

    def fake_assess(_config, name: str) -> DatabaseDumpability:
        if name == "scytaledroid_core_prod":
            return DatabaseDumpability(
                database=name,
                view_count=51,
                issues=["view `v_masvs_matrix` is not dumpable (collation mix a vs b)"],
            )
        return DatabaseDumpability(database=name, view_count=3)

    report = try_assess_sources_dumpability(
        ["android_permission_intel", "scytaledroid_core_prod"],
        config=config,
        assess_one=fake_assess,
    )
    assert report.blocked_names() == {"scytaledroid_core_prod"}
    assert report.issue_lines()[0].startswith("scytaledroid_core_prod:")


def test_try_assess_without_connection_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.database.mariadb.session.try_load_mariadb_config",
        lambda: None,
    )
    report = try_assess_sources_dumpability(["erebus_threat_intel_prod"])
    assert report.entries == []


def test_print_dumpability_report_lists_blocked_views(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.backup.terminal.dumpability import print_dumpability_report
    from mercury.backup.dump_preflight import SourcesDumpability

    print_dumpability_report(
        SourcesDumpability(
            entries=[
                DatabaseDumpability(
                    database="scytaledroid_core_prod",
                    view_count=51,
                    issues=["view `v_masvs_matrix` is not dumpable (collation mix a vs b)"],
                )
            ]
        )
    )
    out = capsys.readouterr().out
    assert "DUMPABILITY" in out.upper() or "Dumpability" in out
    assert "v_masvs_matrix" in out
    assert "will not skip views" in out.lower() or "partial dump" in out.lower()
    assert "1 of 1 source(s) blocked" in out
    assert "ScytaleDroid" in out


def test_print_dumpability_report_counts_dumpable_sources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.terminal.dumpability import print_dumpability_report
    from mercury.backup.dump_preflight import SourcesDumpability

    print_dumpability_report(
        SourcesDumpability(
            entries=[
                DatabaseDumpability(database="erebus_threat_intel_prod", view_count=3),
                DatabaseDumpability(
                    database="scytaledroid_core_prod",
                    view_count=51,
                    issues=["view `v_masvs_matrix` is not dumpable (collation mix a vs b)"],
                ),
            ]
        )
    )
    out = capsys.readouterr().out
    assert "1 of 2 source(s) blocked" in out
    assert "1 dumpable" in out


def test_fetch_live_view_names_by_database() -> None:
    from mercury.backup.dump_preflight import fetch_live_view_names_by_database

    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")

    def fake_query(_config, sql: str) -> str:
        assert "IN (" in sql
        return (
            "erebus_threat_intel_prod\tv_ok\n"
            "scytaledroid_core_prod\tv_masvs_matrix\n"
        )

    grouped = fetch_live_view_names_by_database(
        config,
        ["erebus_threat_intel_prod", "scytaledroid_core_prod"],
        query_fn=fake_query,
    )
    assert grouped["erebus_threat_intel_prod"] == ["v_ok"]
    assert grouped["scytaledroid_core_prod"] == ["v_masvs_matrix"]


def test_try_assess_uses_one_force_script(monkeypatch: pytest.MonkeyPatch) -> None:
    config = MariaDbConnectionConfig(host="127.0.0.1", user="reader")
    monkeypatch.setattr(
        "mercury.backup.dump_preflight.fetch_live_view_names_by_database",
        lambda *_a, **_k: {
            "erebus_threat_intel_prod": ["v_ok"],
            "scytaledroid_core_prod": ["v_masvs_matrix"],
        },
    )
    monkeypatch.setattr(
        "mercury.backup.dump_preflight._client_force_script",
        lambda *_a, **_k: (0, "", FORCE_STDERR),
    )
    report = try_assess_sources_dumpability(
        ["erebus_threat_intel_prod", "scytaledroid_core_prod"],
        config=config,
    )
    assert report.dumpable_count == 1
    assert report.blocked_names() == {"scytaledroid_core_prod"}
    assert "`v_masvs_matrix`" in report.issue_lines()[0]


def test_parse_force_show_create_errors_qualified() -> None:
    from mercury.backup.dump_preflight import parse_force_show_create_errors_qualified

    parsed = parse_force_show_create_errors_qualified(FORCE_STDERR)
    assert ("scytaledroid_core_prod", "v_masvs_matrix") in parsed


def test_format_undumpable_next_action_names_the_view() -> None:
    from mercury.backup.dump_preflight import format_undumpable_next_action

    line = format_undumpable_next_action(
        ["scytaledroid_core_prod"],
        [
            "scytaledroid_core_prod: view `v_masvs_matrix` is not dumpable "
            "(collation mix utf8mb4_uca1400_ai_ci vs utf8mb4_general_ci)."
        ],
    )
    assert line == (
        "Recreate undumpable ScytaleDroid view `v_masvs_matrix`, "
        "then rerun Backup production."
    )


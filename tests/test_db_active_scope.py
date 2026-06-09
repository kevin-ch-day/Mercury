"""Tests for active Mercury database scope snapshot."""

from __future__ import annotations

import pytest

from mercury.database.mariadb.active_scope import fetch_active_scope_report
from mercury.database.terminal.active_scope import print_active_scope_report


class _Config:
    use_client = True


def test_fetch_active_scope_report_uses_one_query() -> None:
    calls: list[str] = []

    def rows_fn(_config, sql: str) -> list[list[str]]:
        calls.append(sql)
        return [
            ["android_permission_intel", "1", "41", "35", "309088780"],
            ["erebus_threat_intel_dev", "1", "10", "0", "2048"],
            ["erebus_threat_intel_prod", "1", "12", "1", "4096"],
            ["scytaledroid_core_dev", "0", "0", "0", "0"],
            ["scytaledroid_core_prod", "1", "22", "2", "8192"],
        ]

    report = fetch_active_scope_report(_Config(), rows_fn=rows_fn)
    assert len(calls) == 1
    assert "information_schema.schemata" in calls[0]
    assert report.database_count == 5
    assert report.present_count == 4
    assert report.missing_count == 1
    android = next(row for row in report.rows if row.name == "android_permission_intel")
    assert android.sync_role == "backup-only"
    prod = next(row for row in report.rows if row.name == "erebus_threat_intel_prod")
    assert prod.sync_role == "source+pair"
    dev = next(row for row in report.rows if row.name == "erebus_threat_intel_dev")
    assert dev.sync_role == "dev target"


def test_print_active_scope_report_compact(capsys: pytest.CaptureFixture[str]) -> None:
    report = fetch_active_scope_report(
        _Config(),
        rows_fn=lambda _config, _sql: [
            ["android_permission_intel", "1", "41", "35", "309088780"],
            ["erebus_threat_intel_dev", "1", "10", "0", "2048"],
            ["erebus_threat_intel_prod", "1", "12", "1", "4096"],
            ["scytaledroid_core_dev", "0", "0", "0", "0"],
            ["scytaledroid_core_prod", "1", "22", "2", "8192"],
        ],
    )
    print_active_scope_report(report, compact=True)
    out = capsys.readouterr().out
    assert "Access mode:" in out
    assert "Present:" in out
    assert "DATABASE" in out
    assert "STATUS" in out
    assert "SYNC ROLE" in out
    assert "android_permission_intel" in out
    assert "backup-only" in out
    assert "source+pair" in out
    assert "dev target" in out

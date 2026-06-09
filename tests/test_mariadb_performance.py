"""Tests for MariaDB read-only performance helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.database.mariadb.client import run_client_script
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.readonly_session import probe_server_facts
from mercury.database.mariadb.stats import fetch_all_database_stats

from tests.conftest import (
    DEFAULT_MARIADB_SOCKET,
    live_mariadb_client_config,
    mariadb_client_connects,
    platform_prod_databases_present,
)

SOCKET_PATH = DEFAULT_MARIADB_SOCKET


def _mariadb_socket_available(path: Path = SOCKET_PATH) -> bool:
    return mariadb_client_connects(path)


def _client_config():
    return live_mariadb_client_config()


@pytest.mark.skipif(not _mariadb_socket_available(), reason="MariaDB socket not present")
class TestMariaDbPerformanceIntegration:
    def test_run_client_script_probe(self) -> None:
        raw = run_client_script(
            _client_config(),
            "SELECT 1;\nSELECT VERSION();\nSELECT CURRENT_USER();\nSHOW DATABASES;",
        )
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        assert lines[0] == "1"
        assert "MariaDB" in lines[1] or "mysql" in lines[1].lower()
        assert "@" in lines[2]

    def test_probe_server_facts_single_round_trip_client(self) -> None:
        if not platform_prod_databases_present():
            pytest.skip("Platform prod databases not present on MariaDB server")
        facts = probe_server_facts(_client_config())
        assert facts["ping_ok"] == "1"
        assert facts["version"]
        assert facts["current_user"]
        assert "erebus_threat_intel_prod" in facts["database_names"]

    def test_inspect_uses_single_query(self) -> None:
        calls: list[str] = []

        def row_fn(config, sql: str) -> list[str]:
            calls.append(sql)
            return ["1", "2", "0", "4096"]

        result = inspect_database_on_server(
            "erebus_threat_intel_prod",
            _client_config(),
            row_fn=row_fn,
        )
        assert result.exists_on_server is True
        assert result.table_count == 2
        assert len(calls) == 1
        assert "information_schema.schemata" in calls[0]

    def test_fetch_all_database_stats(self) -> None:
        if not platform_prod_databases_present():
            pytest.skip("Platform prod databases not present on MariaDB server")
        report = fetch_all_database_stats(_client_config())
        assert report.databases
        names = {entry.name for entry in report.databases}
        assert "erebus_threat_intel_prod" in names
        assert report.total_bytes >= 0

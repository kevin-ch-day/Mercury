"""Synthetic tests for the fail-closed post-restore acceptance contract."""

from __future__ import annotations

import pytest

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.restore.acceptance import (
    AcceptanceQuery,
    ReadOnlyAcceptanceError,
    assert_readonly_acceptance_sql,
    phase2a_readonly_acceptance_contract,
    run_readonly_acceptance,
    validate_acceptance_contract,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SHOW DATABASES",
        "EXPLAIN SELECT 1",
    ],
)
def test_acceptance_allowlist_permits_only_readonly_statement_starts(sql: str) -> None:
    assert_readonly_acceptance_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO x VALUES (1)",
        "SELECT 1; DELETE FROM x",
        "SELECT 1 -- hidden write",
        "SELECT * FROM x FOR UPDATE",
        "CALL refresh_cache()",
    ],
)
def test_acceptance_allowlist_fails_closed(sql: str) -> None:
    with pytest.raises(ReadOnlyAcceptanceError):
        assert_readonly_acceptance_sql(sql)


def test_acceptance_validates_every_query_before_connecting() -> None:
    queries = [
        AcceptanceQuery(check_id="safe", description="safe", sql="SELECT 1"),
        AcceptanceQuery(check_id="unsafe", description="unsafe", sql="DELETE FROM x"),
    ]
    called = False

    def never_called(_config, _sql):
        nonlocal called
        called = True
        return []

    with pytest.raises(ReadOnlyAcceptanceError):
        run_readonly_acceptance(MariaDbConnectionConfig(host="localhost", user="test"), queries, scalars=never_called)
    assert called is False


def test_phase2a_contract_runs_with_synthetic_expected_values() -> None:
    queries = phase2a_readonly_acceptance_contract()
    expected = {query.check_id: query.expected for query in queries}

    def fake_scalars(_config, sql: str) -> list[str]:
        query = next(item for item in queries if item.sql == sql)
        return [str(expected[query.check_id])]

    report = run_readonly_acceptance(
        MariaDbConnectionConfig(host="localhost", user="test"),
        queries,
        scalars=fake_scalars,
    )
    assert report.passed is True
    assert len(report.results) == 9
    assert next(result for result in report.results if result.check_id == "erebus_procedure_names").passed
    assert next(
        result for result in report.results if result.check_id == "obsidiandroid_core_objects"
    ).passed


def test_contract_rejects_duplicate_check_ids() -> None:
    with pytest.raises(ReadOnlyAcceptanceError, match="Duplicate"):
        validate_acceptance_contract(
            [
                AcceptanceQuery(check_id="same", description="one", sql="SELECT 1"),
                AcceptanceQuery(check_id="same", description="two", sql="SELECT 2"),
            ]
        )

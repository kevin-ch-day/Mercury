"""Tests for domain log event helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.logging import configure_logging, current_backup_log_file, current_database_log_file
from mercury.logging.events import log_batch_backup, log_inventory_discovered, log_verification_result

from tests.logging.helpers import read_log


@pytest.mark.parametrize(
    ("event", "kwargs", "log_reader", "needles"),
    [
        (
            log_inventory_discovered,
            {"mode": "live", "count": 7, "connection": "socket"},
            current_database_log_file,
            ("inventory discovered", "count=7", "mercury.database"),
        ),
        (
            log_verification_result,
            {
                "database": "prod_db",
                "verified": False,
                "issue_count": 2,
                "backup_id": "abc",
            },
            current_backup_log_file,
            ("verification", "verified=False", "mercury.backup"),
        ),
        (
            log_batch_backup,
            {
                "backup_kind": "full",
                "execute": False,
                "source_count": 3,
                "executed": 0,
                "dry_run": 3,
                "refused": 0,
                "errors": 0,
            },
            current_backup_log_file,
            ("batch backup", "dry_run=3"),
        ),
    ],
)
def test_domain_events_write_to_dedicated_logs(
    log_dir: Path,
    event,
    kwargs: dict,
    log_reader,
    needles: tuple[str, ...],
) -> None:
    configure_logging(enabled=True, log_dir=log_dir)
    event(**kwargs)
    text = read_log(log_reader)
    for needle in needles:
        assert needle in text

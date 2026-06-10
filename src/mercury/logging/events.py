"""Domain event logging — structured activity hooks for operations."""

from __future__ import annotations

import logging

from mercury.logging.engine import get_logger


def log_inventory_discovered(*, mode: str, count: int, connection: str) -> None:
    get_logger("mercury.database").info(
        "inventory discovered mode=%s count=%s connection=%s",
        mode,
        count,
        connection,
    )


def log_inventory_fallback(*, reason: str, fallback: str) -> None:
    get_logger("mercury.database").warning(
        "inventory fallback reason=%s fallback=%s",
        reason,
        fallback,
    )


def log_mariadb_probe(*, connected: bool, latency_ms: float | None = None, database_count: int | None = None, error: str | None = None) -> None:
    log = get_logger("mercury.database")
    if connected:
        log.info(
            "mariadb probe ok latency_ms=%s database_count=%s",
            latency_ms,
            database_count,
        )
    else:
        log.warning("mariadb probe failed error=%s", error or "unknown")


def log_verification_result(*, database: str, verified: bool, issue_count: int, backup_id: str, updated_manifest: bool = False) -> None:
    level = logging.INFO if verified else logging.WARNING
    get_logger("mercury.backup").log(
        level,
        "verification database=%s verified=%s issues=%s backup_id=%s updated_manifest=%s",
        database,
        verified,
        issue_count,
        backup_id,
        updated_manifest,
    )


def log_verify_all_summary(*, passed: int, failed: int, skipped: int, sources: int) -> None:
    level = logging.INFO if failed == 0 and skipped == 0 else logging.WARNING
    get_logger("mercury.backup").log(
        level,
        "verify-all passed=%s failed=%s skipped=%s sources=%s",
        passed,
        failed,
        skipped,
        sources,
    )


def log_restore_check(*, database: str, allowed: bool, blocker_count: int) -> None:
    get_logger("mercury.backup").info(
        "restore-check database=%s allowed=%s blockers=%s",
        database,
        allowed,
        blocker_count,
    )


def log_target_completeness(
    *,
    database: str,
    status: str,
    live_objects: int | None,
    backup_objects: int | None,
    missing_critical: int,
) -> None:
    level = logging.INFO if status == "complete" else logging.WARNING
    get_logger("mercury.restore").log(
        level,
        "target completeness database=%s status=%s live_objects=%s backup_objects=%s missing_critical=%s",
        database,
        status,
        live_objects,
        backup_objects,
        missing_critical,
    )


def log_env_probe(*, connected: bool, database_status: str) -> None:
    get_logger("mercury.database").info(
        "env probe connected=%s database_status=%s",
        connected,
        database_status,
    )


def log_batch_backup(*, backup_kind: str, execute: bool, source_count: int, executed: int, dry_run: int, refused: int, errors: int) -> None:
    get_logger("mercury.backup").info(
        "batch backup kind=%s execute=%s sources=%s executed=%s dry_run=%s refused=%s errors=%s",
        backup_kind,
        execute,
        source_count,
        executed,
        dry_run,
        refused,
        errors,
    )


def log_sync_readiness(*, mode: str, ready: int, blocked: int) -> None:
    get_logger("mercury.sync").info(
        "sync readiness mode=%s ready=%s blocked=%s",
        mode,
        ready,
        blocked,
    )


def log_menu_action(*, choice: str, title: str, result: str) -> None:
    get_logger("mercury.menu").info(
        "menu action choice=%s title=%s result=%s",
        choice,
        title,
        result,
    )


def log_database_error(*, operation: str, error: str) -> None:
    get_logger("mercury.database").error(
        "database error operation=%s error=%s",
        operation,
        error,
    )

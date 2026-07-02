"""Handoff package status labels (dependency-free; safe for ledger imports)."""

_HANDOFF_RANK = {
    "complete": 0,
    "complete with warnings": 1,
    "partial": 2,
    "empty": 3,
}


def database_bundle_package_status(
    *,
    source_count: int,
    verified_count: int,
    missing_count: int,
    failed_count: int,
    stale_count: int = 0,
    unknown_freshness_count: int = 0,
) -> str:
    if source_count == 0:
        return "empty"
    complete = (
        verified_count == source_count
        and failed_count == 0
        and missing_count == 0
    )
    if complete:
        if stale_count or unknown_freshness_count:
            return "complete with warnings"
        return "complete"
    return "partial"


def combine_handoff_status(*statuses: str) -> str:
    if not statuses:
        return "empty"
    return max(statuses, key=lambda status: _HANDOFF_RANK.get(status, 2))


def handoff_write_requires_force(package_status: str) -> bool:
    return package_status != "complete"


def handoff_write_ack_prompt(package_status: str) -> str:
    if package_status == "partial":
        return (
            "Handoff package is partial (missing or failed sources). "
            "Write manifest/runbook files to USB anyway?"
        )
    if package_status == "complete with warnings":
        return (
            "Handoff package has freshness warnings (stale or unknown backups). "
            "Write to USB anyway?"
        )
    return "Write manifest and runbook files to USB?"


def handoff_write_cli_error(package_status: str) -> str:
    if package_status == "partial":
        return (
            "Handoff package is partial — missing or failed sources remain. "
            "Run full backup and verify, or re-run with --force to write anyway."
        )
    return (
        "Handoff package is not fully fresh — stale or unknown backups remain. "
        "Run full backup before handoff, or re-run with --force to write anyway."
    )

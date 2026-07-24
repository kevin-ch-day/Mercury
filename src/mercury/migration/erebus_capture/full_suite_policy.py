"""Exact, fail-closed policy for full-suite validation summaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpectedFailure:
    node_id: str
    classification: str
    disposition: str = "accepted_unrelated"


@dataclass(frozen=True)
class FullSuiteSummary:
    command: str
    return_code: int
    collected_count: int
    passed_count: int
    failed: tuple[ExpectedFailure, ...]
    skipped_count: int
    collection_errors: int = 0
    interrupted: bool = False
    focused_failures: int = 0
    dependency_valid: bool = True


def evaluate(summary: FullSuiteSummary, approved: tuple[ExpectedFailure, ...] = ()) -> tuple[bool, str]:
    """Accept only a clean suite or an exact approved identity/classification set."""
    if (summary.collection_errors or summary.interrupted or summary.focused_failures or
            not summary.dependency_valid or not summary.command or summary.collected_count < 0):
        return False, "FULL_SUITE_STRUCTURAL_FAILURE"
    if not summary.failed:
        return (summary.return_code == 0, "FULL_SUITE_PASS" if summary.return_code == 0 else "FULL_SUITE_RETURN_CODE")
    if summary.return_code == 0:
        return False, "FULL_SUITE_INCONSISTENT_RETURN_CODE"
    actual = {(item.node_id, item.classification, item.disposition) for item in summary.failed}
    allowed = {(item.node_id, item.classification, item.disposition) for item in approved}
    return (actual == allowed, "FULL_SUITE_APPROVED_EXCEPTIONS" if actual == allowed else "FULL_SUITE_UNEXPECTED_FAILURES")

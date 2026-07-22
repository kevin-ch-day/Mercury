"""Terminal printers for cleanup status/preview."""

from __future__ import annotations

from mercury import output
from mercury.storage.cleanup import CleanupPreviewReport, CleanupStatusReport


def _fmt_gib(num_bytes: int) -> str:
    return f"{num_bytes / (1024**3):.2f} GiB"


def print_cleanup_status(report: CleanupStatusReport) -> int:
    output.heading("Storage cleanup status")
    output.field("Protected size", _fmt_gib(report.protected_size_bytes))
    output.field("Manual-review size", _fmt_gib(report.manual_review_size_bytes))
    output.field("Routine-retained size", _fmt_gib(report.routine_retained_size_bytes))
    output.field("Safe-candidate estimate", _fmt_gib(report.safe_candidate_estimate_bytes))
    output.field("ScytaleDroid excluded size", _fmt_gib(report.scytaledroid_excluded_size_bytes))
    output.field("Last audit timestamp", report.last_audit_timestamp)
    output.field(
        "Destination validation",
        "pending" if report.destination_validation_pending else "validated",
    )
    output.field("Cleanup execution", report.cleanup_execution_state)
    for note in report.notes:
        output.write(f"  note: {note}")
    return 0


def print_cleanup_preview(report: CleanupPreviewReport) -> int:
    output.heading("Storage cleanup preview")
    output.field("Mount", report.mount_root)
    output.field("Generated", report.generated_at)
    output.field("Execute", f"refused ({report.execute_refused_reason})")
    if report.plan_written:
        output.field("Plan file", report.plan_written)
    for entry in report.entries:
        output.write(
            f"  [{entry.classification.value}] {entry.path} · {entry.reason}"
        )
    return 0

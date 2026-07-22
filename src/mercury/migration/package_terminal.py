"""Terminal output for destination package preview."""

from __future__ import annotations

from mercury import output
from mercury.migration.destination_package import DestinationPackagePreview


def _fmt_gib(num_bytes: int) -> str:
    return f"{num_bytes / (1024**3):.2f} GiB"


def print_destination_package_preview(report: DestinationPackagePreview) -> int:
    output.heading("Destination package preview")
    output.field("Preview ID", report.preview_id or "—")
    output.field("Run ID", report.run_id)
    output.field("Mount", report.mount_root)
    output.field("Estimated size", _fmt_gib(report.estimated_size_bytes))
    output.field("File count", str(report.file_count))
    output.field("Manifest/reference count", str(report.manifest_reference_count))
    output.field("Uses unqualified latest", "yes" if report.uses_unqualified_latest else "no")
    output.write("Included members:")
    for member in report.included:
        output.write(
            f"  + [{member.kind}] {member.identity} · {member.mode} · "
            f"{_fmt_gib(member.size_bytes)} · {member.path}"
        )
    if report.intake_included or report.intake_excluded:
        output.write("Erebus intake subset:")
        for name in report.intake_included:
            output.write(f"  include: erebus-intake/{name}")
        for name in report.intake_excluded:
            output.write(f"  exclude: erebus-intake/{name}")
    output.write("Excluded top-level trees:")
    for name in report.excluded_top_level:
        output.write(f"  - {name}")
    if report.included_backup_ids:
        output.write("Backup IDs:")
        for backup_id in report.included_backup_ids:
            output.write(f"  · {backup_id}")
    if report.included_capture_ids:
        output.write("Capture IDs:")
        for capture_id in report.included_capture_ids:
            output.write(f"  · {capture_id}")
    if report.included_git_commits:
        output.write("Git identities:")
        for commit in report.included_git_commits:
            output.write(f"  · {commit}")
    if report.unresolved:
        output.write("Unresolved:")
        for item in report.unresolved:
            output.write(f"  ! {item}")
    if report.errors:
        output.write("Errors:")
        for err in report.errors:
            output.write(f"  !! {err}")
    output.field("Preview ok", "yes" if report.ok else "no")
    return 0 if report.ok else 1

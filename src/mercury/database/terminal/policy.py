"""Display policy validation reports."""

from mercury import output
from mercury.database.policy import PolicyReport


def print_policy_report(report: PolicyReport) -> None:
    output.heading("Config policy validation")
    output.field("config_file", report.config_file or "(none)")
    output.field("databases_checked", report.databases_checked)
    output.field("ok", report.ok())

    for level in ("error", "warning", "info"):
        items = [f for f in report.findings if f.level == level]
        if not items:
            continue
        output.heading(level.upper())
        for finding in items:
            prefix = f"{finding.database}: " if finding.database else ""
            output.item(f"{prefix}{finding.message}")

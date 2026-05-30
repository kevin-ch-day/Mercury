"""Validate database names against Mercury backup policy."""

import mercury.paths as paths
from pydantic import BaseModel, Field

from mercury.database.core import (
    DatabaseRole,
    classify_database,
    configured_database_names,
    load_databases_from_file,
)
from mercury.database.discovery import discover_from_config
from mercury.database.prod_dev_pairs import build_prod_dev_pairs


class PolicyFinding(BaseModel):
    level: str  # error | warning | info
    database: str | None = None
    message: str


class PolicyReport(BaseModel):
    config_file: str | None = None
    databases_checked: int = 0
    findings: list[PolicyFinding] = Field(default_factory=list)

    @property
    def errors(self) -> list[PolicyFinding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[PolicyFinding]:
        return [f for f in self.findings if f.level == "warning"]

    def ok(self) -> bool:
        return len(self.errors) == 0


def _add(report: PolicyReport, level: str, message: str, database: str | None = None) -> None:
    report.findings.append(PolicyFinding(level=level, database=database, message=message))


def validate_config_policy(*, use_demo_catalog: bool = False) -> PolicyReport:
    """Check database names in config (or demo inventory) against backup policy."""
    report = PolicyReport()

    if use_demo_catalog:
        inventory = discover_from_config(include_catalog=True)
        names = inventory.names
        report.config_file = "demo/catalog"
    else:
        if paths.DATABASES_LOCAL.exists():
            report.config_file = str(paths.DATABASES_LOCAL)
            raw = load_databases_from_file(paths.DATABASES_LOCAL)
        elif paths.DATABASES_EXAMPLE.exists():
            report.config_file = str(paths.DATABASES_EXAMPLE)
            raw = load_databases_from_file(paths.DATABASES_EXAMPLE)
        else:
            _add(report, "error", "No databases.toml or databases.example.toml found.")
            return report
        names = sorted(raw.keys())

    report.databases_checked = len(names)
    if not names:
        _add(report, "warning", "No databases defined in config.")
        return report

    for name in names:
        c = classify_database(name)
        if c.role == DatabaseRole.DEVELOPMENT:
            _add(
                report,
                "warning",
                "Dev database in config — must never be a backup source; sync target only.",
                name,
            )
        if c.manual_review:
            _add(report, "warning", "Unknown naming pattern — manual review required.", name)
        if c.role == DatabaseRole.RESTORE_CHECK_TEMP:
            _add(report, "info", "Restore-check temp DB — exclude from backup plans.", name)
        if c.backup_source:
            _add(report, "info", "Eligible backup source (production or shared authority).", name)

    pairs = build_prod_dev_pairs(names)
    for pair in pairs:
        if not pair.dev_listed:
            _add(
                report,
                "warning",
                f"Prod '{pair.prod}' has no '{pair.expected_dev}' in config — sync target missing.",
                pair.prod,
            )

    for name in names:
        c = classify_database(name)
        if c.role == DatabaseRole.DEVELOPMENT and c.backup_source:
            _add(report, "error", "Dev database incorrectly marked as backup source.", name)

    return report


def validate_configured_names_only() -> PolicyReport:
    """Validate only databases.toml / example (no catalog merge)."""
    report = PolicyReport()
    names = configured_database_names()
    report.config_file = (
        str(paths.DATABASES_LOCAL)
        if paths.DATABASES_LOCAL.exists()
        else str(paths.DATABASES_EXAMPLE)
    )
    report.databases_checked = len(names)
    if not names:
        _add(report, "warning", "No databases in config files.")
        return report

    for name in names:
        c = classify_database(name)
        if c.role == DatabaseRole.DEVELOPMENT:
            _add(report, "warning", "Dev DB in config — not a backup source.", name)
        if c.manual_review:
            _add(report, "warning", "Manual review required.", name)
    return report

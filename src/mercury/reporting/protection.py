"""Protection status: what is backed up, what is not, and prod→dev readiness (dry-run)."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from mercury.backup.status import build_backup_status_report
from mercury.database import (
    BackupPlanDryRun,
    DatabaseInventory,
    build_backup_plan,
    discover_demo,
)
from mercury.database.core import (
    CATALOG_BY_NAME,
    DatabaseRole,
    classify_database,
    filter_inventory,
    shared_authority_note,
    source_role_label,
)
from mercury.database.prod_dev_pairs import ProdDevPair, build_prod_dev_pairs, orphan_dev_databases
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import operator_status
from mercury.core.safety import MODE_SEED
from mercury.terminal.format import format_human_datetime


class ProtectionReport(BaseModel):
    generated_at: str
    mode: str
    connection: str
    operator: dict[str, str] = Field(default_factory=dict)
    backup_plan: BackupPlanDryRun
    inventory_count: int
    ignored_out_of_scope_count: int = 0
    protected: list[str] = Field(default_factory=list)
    not_protected: list[str] = Field(default_factory=list)
    shared_authority: list[str] = Field(default_factory=list)
    source_statuses: dict[str, str] = Field(default_factory=dict)
    verified_source_count: int = 0
    missing_source_count: int = 0
    failed_source_count: int = 0
    manual_review: list[str] = Field(default_factory=list)
    prod_dev_pairs: list[ProdDevPair] = Field(default_factory=list)
    orphan_dev: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)


def build_protection_report(*, live: bool = False, probe_database: bool = False) -> ProtectionReport:
    """Build protection snapshot from demo/catalog or live server inventory."""
    if live:
        from mercury.database.discovery import discover

        inventory: DatabaseInventory = discover("live")
    else:
        inventory = discover_demo()
    scoped_inventory = filter_inventory(inventory)
    ignored_out_of_scope_count = inventory.count - scoped_inventory.count
    names = [e.name for e in scoped_inventory.entries]
    projects = {e.name: e.project for e in scoped_inventory.entries if e.project}

    plan = build_backup_plan(names)
    pairs = build_prod_dev_pairs(names, projects=projects)
    orphans = orphan_dev_databases(names, pairs)

    protected = list(plan.backup_sources)
    not_protected = [e.name for e in plan.excluded]

    shared: list[str] = []
    review: list[str] = []
    for name in names:
        c = classify_database(name)
        if c.role == DatabaseRole.SHARED_AUTHORITY:
            shared.append(name)
        if c.manual_review:
            review.append(name)

    policy = load_execution_policy()
    backup_status = build_backup_status_report(
        live=live,
        selected=list(plan.backup_sources),
        policy=policy,
    )
    actions: list[str] = []
    if policy.live_execution_allowed():
        actions.append("Run: mercury backup run --db <prod> --kind full --execute")
        actions.append("Then: mercury backup verify --db <prod> --update-manifest")
    else:
        actions.append("Run: mercury backup run --db <prod> --kind full (dry-run plan)")
        actions.append("Enable live_actions in config/local.toml before --execute")
        actions.append("After backup: mercury backup verify --db <prod> --update-manifest")
    for pair in pairs:
        if not pair.dev_listed:
            actions.append(f"Add or discover dev target for prod: {pair.prod}")
        else:
            actions.append(
                f"Before refreshing {pair.expected_dev} from {pair.prod}: protect the source with a full verified backup first."
            )
    for name in review:
        actions.append(f"Manual review required: {name}")
    if not policy.live_execution_allowed():
        actions.append("Dry-run mode: backup/sync execution requires explicit live enable.")

    return ProtectionReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="live" if live else MODE_SEED,
        connection=inventory.connection,
        operator=operator_status(probe_database=probe_database or live),
        backup_plan=plan,
        inventory_count=scoped_inventory.count,
        ignored_out_of_scope_count=ignored_out_of_scope_count,
        protected=protected,
        not_protected=not_protected,
        shared_authority=shared,
        source_statuses={entry.database: entry.protection_status for entry in backup_status.entries},
        verified_source_count=backup_status.verified_count,
        missing_source_count=backup_status.missing_count,
        failed_source_count=backup_status.failed_count,
        manual_review=review,
        prod_dev_pairs=pairs,
        orphan_dev=orphans,
        action_items=actions,
    )


def format_protection_report(report: ProtectionReport, *, compact: bool = False) -> str:
    """Render report as plain text (for terminal or file)."""
    if compact:
        return _format_protection_report_compact(report)

    lines: list[str] = [
        "MERCURY PROTECTION STATUS",
        f"Generated: {format_human_datetime(report.generated_at)}",
        f"Mode: {report.mode} | Connection: {report.connection}",
        "",
        "Operator status:",
    ]
    for key, value in report.operator.items():
        lines.append(f"  {key}: {value}")
    production_sources = [name for name in report.protected if name not in report.shared_authority]
    lines.extend(
        [
            "",
            f"Active scope: {report.inventory_count} databases",
            "",
            "PRODUCTION SOURCES",
        ]
    )
    if report.ignored_out_of_scope_count:
        lines.insert(len(lines) - 1, f"Ignored out of scope: {report.ignored_out_of_scope_count} databases")
    for name in production_sources:
        entry = CATALOG_BY_NAME.get(name)
        project = f" [{entry.project}]" if entry else ""
        lines.append(f"  + {name}{project}")
    if not production_sources:
        lines.append("  (none)")

    lines.append("")
    lines.append("SHARED AUTHORITY SOURCES")
    for name in report.shared_authority:
        lines.append(f"  * {name}")
        lines.append("    backup-only; no dev sync pair by design")
    if not report.shared_authority:
        lines.append("  (none)")

    lines.append("")
    lines.append("EXCLUDED FROM BACKUP")
    for name in report.not_protected:
        excluded = next((e for e in report.backup_plan.excluded if e.name == name), None)
        reason = excluded.reason if excluded else ""
        lines.append(f"  - {name}: {reason}")

    lines.append("")
    lines.append("PRODUCTION SYNC PAIRS")
    for pair in report.prod_dev_pairs:
        dev_status = pair.expected_dev if pair.dev_listed else f"MISSING ({pair.expected_dev})"
        project = f" [{pair.project}]" if pair.project else ""
        lines.append(f"  {pair.prod}{project}")
        lines.append(f"    -> {dev_status}")
        lines.append(f"    {pair.sync_notes}")
    lines.append(f"  {shared_authority_note()}")

    if report.orphan_dev:
        lines.append("")
        lines.append("DEV DATABASES WITHOUT MATCHING PROD IN INVENTORY")
        for name in report.orphan_dev:
            lines.append(f"  - {name}")

    lines.append("")
    lines.append("ACTION ITEMS")
    for item in report.action_items:
        lines.append(f"  - {item}")

    lines.append("")
    policy = load_execution_policy()
    lines.append(f"Live actions enabled: {policy.live_actions_enabled}")
    lines.append(f"Dry-run: {policy.dry_run}")
    return "\n".join(lines)


def _format_protection_report_compact(report: ProtectionReport) -> str:
    lines: list[str] = [
        "PROTECTION SUMMARY",
        f"  active scope: {report.inventory_count} databases",
        "",
        "Production sources:",
    ]
    if report.ignored_out_of_scope_count:
        lines.insert(2, f"  ignored out of scope: {report.ignored_out_of_scope_count} databases")
    for name in [name for name in report.protected if name not in report.shared_authority]:
        entry = CATALOG_BY_NAME.get(name)
        project = f" [{entry.project}]" if entry else ""
        lines.append(f"  + {name}{project}")
    if not [name for name in report.protected if name not in report.shared_authority]:
        lines.append("  (none)")

    lines.append("")
    lines.append("Shared authority sources:")
    for name in report.shared_authority:
        lines.append(f"  * {name} (backup-only)")
    if not report.shared_authority:
        lines.append("  (none)")

    missing_dev = [p for p in report.prod_dev_pairs if not p.dev_listed]
    if missing_dev:
        lines.append("")
        lines.append("Missing dev targets:")
        for pair in missing_dev:
            lines.append(f"  - {pair.prod} -> {pair.expected_dev}")

    if report.manual_review:
        lines.append("")
        lines.append("Manual review:")
        for name in report.manual_review:
            lines.append(f"  - {name}")

    return "\n".join(lines)


def print_protection_report(report: ProtectionReport, *, compact: bool = False) -> None:
    """Render protection report to the terminal."""
    from mercury.terminal import screen as display_screen

    if not compact:
        display_screen.write_summary(format_protection_report(report, compact=False))
        return

    production_sources = [name for name in report.protected if name not in report.shared_authority]
    display_screen.write_fields(
        {
            "Active scope": report.inventory_count,
            "Production sources": len(production_sources),
            "Shared authority": len(report.shared_authority),
            "Sync pairs": len(report.prod_dev_pairs),
            "Verified sources": report.verified_source_count,
            "Missing sources": report.missing_source_count,
            "Failed sources": report.failed_source_count,
        }
    )
    if report.ignored_out_of_scope_count:
        display_screen.write_fields({"Ignored out of scope": report.ignored_out_of_scope_count})
    if report.protected:
        rows = []
        for name in report.protected:
            entry = CATALOG_BY_NAME.get(name)
            project = entry.project if entry and entry.project else "—"
            source_role = source_role_label(name)
            status = report.source_statuses.get(name, "unknown")
            rows.append([name, source_role, status, project])
        display_screen.write_blank()
        display_screen.write_compact_table(
            ["SOURCE DATABASE", "SOURCE ROLE", "STATUS", "PROJECT"],
            rows,
            min_col_widths=[28, 16, 10, 12],
            max_col_widths=[36, 24, 14, 16],
        )
    else:
        display_screen.write_status("warn", "No protected backup sources yet")

"""Protection status: what is backed up, what is not, and prod→dev readiness (dry-run)."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from mercury.database import (
    BackupPlanDryRun,
    DatabaseInventory,
    build_backup_plan,
    discover_demo,
)
from mercury.database.core import CATALOG_BY_NAME, DatabaseRole, classify_database
from mercury.database.pairs import ProdDevPair, build_prod_dev_pairs, orphan_dev_databases
from mercury.runtime import operator_status
from mercury.safety import DRY_RUN_ONLY, LIVE_ACTIONS_ENABLED, MODE_SEED


class ProtectionReport(BaseModel):
    generated_at: str
    mode: str
    connection: str
    operator: dict[str, str] = Field(default_factory=dict)
    backup_plan: BackupPlanDryRun
    inventory_count: int
    protected: list[str] = Field(default_factory=list)
    not_protected: list[str] = Field(default_factory=list)
    shared_authority: list[str] = Field(default_factory=list)
    manual_review: list[str] = Field(default_factory=list)
    prod_dev_pairs: list[ProdDevPair] = Field(default_factory=list)
    orphan_dev: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)


def build_protection_report() -> ProtectionReport:
    """Build a full protection snapshot from demo/catalog discovery."""
    inventory: DatabaseInventory = discover_demo()
    names = [e.name for e in inventory.entries]
    projects = {e.name: e.project for e in inventory.entries if e.project}

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

    actions: list[str] = [
        "Run full logical backups for all protected databases (not implemented in seed).",
        "Verify manifests/checksums after each backup (not implemented in seed).",
    ]
    for pair in pairs:
        if not pair.dev_listed:
            actions.append(f"Add or discover dev target for prod: {pair.prod}")
        else:
            actions.append(
                f"Before sync {pair.prod} -> {pair.expected_dev}: backup + verify prod first."
            )
    for name in review:
        actions.append(f"Manual review required: {name}")
    if DRY_RUN_ONLY:
        actions.append("Seed mode: all actions above are planning only.")

    return ProtectionReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode=MODE_SEED,
        connection=inventory.connection,
        operator=operator_status(),
        backup_plan=plan,
        inventory_count=inventory.count,
        protected=protected,
        not_protected=not_protected,
        shared_authority=shared,
        manual_review=review,
        prod_dev_pairs=pairs,
        orphan_dev=orphans,
        action_items=actions,
    )


def format_protection_report(report: ProtectionReport) -> str:
    """Render report as plain text (for terminal or file)."""
    lines: list[str] = [
        "MERCURY PROTECTION STATUS",
        f"Generated: {report.generated_at}",
        f"Mode: {report.mode} | Connection: {report.connection}",
        "",
        "Operator status:",
    ]
    for key, value in report.operator.items():
        lines.append(f"  {key}: {value}")
    lines.extend(
        [
            "",
            f"Inventory: {report.inventory_count} databases",
            "",
            "PROTECTED (backup sources)",
        ]
    )
    for name in report.protected:
        entry = CATALOG_BY_NAME.get(name)
        project = f" [{entry.project}]" if entry else ""
        lines.append(f"  + {name}{project}")
    if not report.protected:
        lines.append("  (none)")

    lines.append("")
    lines.append("NOT PROTECTED (excluded from backup)")
    for name in report.not_protected:
        excluded = next((e for e in report.backup_plan.excluded if e.name == name), None)
        reason = excluded.reason if excluded else ""
        lines.append(f"  - {name}: {reason}")

    if report.shared_authority:
        lines.append("")
        lines.append("SHARED AUTHORITY (backup source; typically no _dev pair)")
        for name in report.shared_authority:
            lines.append(f"  * {name}")

    lines.append("")
    lines.append("PRODUCTION -> DEVELOPMENT PAIRS (sync planning)")
    for pair in report.prod_dev_pairs:
        dev_status = pair.expected_dev if pair.dev_listed else f"MISSING ({pair.expected_dev})"
        project = f" [{pair.project}]" if pair.project else ""
        lines.append(f"  {pair.prod}{project}")
        lines.append(f"    -> {dev_status}")
        lines.append(f"    {pair.sync_notes}")

    if report.orphan_dev:
        lines.append("")
        lines.append("DEV DATABASES WITHOUT MATCHING PROD IN INVENTORY")
        for name in report.orphan_dev:
            lines.append(f"  - {name}")

    lines.append("")
    lines.append("ACTION ITEMS (dry-run)")
    for item in report.action_items:
        lines.append(f"  - {item}")

    lines.append("")
    lines.append(f"Live actions enabled: {LIVE_ACTIONS_ENABLED}")
    return "\n".join(lines)

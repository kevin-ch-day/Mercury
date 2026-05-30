"""Dry-run production → development sync plan (no execution)."""

from pydantic import BaseModel, Field

from mercury.database import discover_demo
from mercury.database.pairs import ProdDevPair, build_prod_dev_pairs
from mercury.core.safety import SYNC_DEV_CONFIRMATION_PHRASE

SYNC_PLAN_NOTES = [
    "Sync is disabled in seed mode.",
    f"Future dev sync will require typing: {SYNC_DEV_CONFIRMATION_PHRASE}",
    "Prerequisite: verified full backup of production source before any sync.",
    "Never drop or overwrite *_prod; target is *_dev only.",
]


class SyncPlanEntry(BaseModel):
    source: str
    target: str
    target_present: bool
    project: str | None = None
    prerequisites: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None


class SyncPlanDryRun(BaseModel):
    mode: str = "dry-run"
    enabled: bool = False
    confirmation_phrase: str = SYNC_DEV_CONFIRMATION_PHRASE
    entries: list[SyncPlanEntry] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def build_sync_plan_demo() -> SyncPlanDryRun:
    """Build prod→dev sync plan from demo inventory."""
    inventory = discover_demo()
    return build_sync_plan_from_inventory(inventory)


def build_sync_plan_from_inventory(inventory) -> SyncPlanDryRun:
    """Build prod→dev sync plan from a discovered inventory."""
    names = [e.name for e in inventory.entries]
    projects = {e.name: e.project for e in inventory.entries if e.project}
    pairs = build_prod_dev_pairs(names, projects=projects)

    plan = SyncPlanDryRun(notes=list(SYNC_PLAN_NOTES))

    for pair in pairs:
        prereq = [
            f"Full backup of {pair.prod}",
            f"Verify backup manifest/checksum for {pair.prod}",
            "Run: mercury sync readiness --live",
        ]
        blocked = None
        if not pair.dev_listed:
            blocked = f"Dev target missing: {pair.expected_dev}"
            plan.skipped.append(pair.prod)
        entry = SyncPlanEntry(
            source=pair.prod,
            target=pair.expected_dev,
            target_present=pair.dev_listed,
            project=pair.project,
            prerequisites=prereq,
            blocked_reason=blocked,
        )
        plan.entries.append(entry)

    return plan


def build_sync_plan_live() -> SyncPlanDryRun:
    """Build prod→dev sync plan from live server inventory."""
    from mercury.database.discovery import discover

    return build_sync_plan_from_inventory(discover("live"))

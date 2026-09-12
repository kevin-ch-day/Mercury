"""Production → development database pairs for sync planning.

The approved pair table is the single authority for:

* ordinary ``*_prod → *_dev`` refresh pairs;
* shared-authority ``android_permission_intel → android_permission_intel_dev``.

Do not derive Permission Intel's target by inventing ``android_permission_intel_prod``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.database.core import DatabaseRole, classify_database
from mercury.database.core.scope import is_in_scope


class ApprovedSyncPairSpec(BaseModel):
    """Explicit, auditable prod→dev mapping for one approved refresh."""

    source: str
    target: str
    project: str
    sync_order: int
    depends_on_sources: tuple[str, ...] = ()
    extra_schema_rewrites: dict[str, str] = Field(default_factory=dict)
    notes: str = "Disposable dev target; sync only after verified source backup."


# Execution order is the tuple order.  Do not sort callers alphabetically.
APPROVED_SYNC_PAIRS: tuple[ApprovedSyncPairSpec, ...] = (
    ApprovedSyncPairSpec(
        source="android_permission_intel",
        target="android_permission_intel_dev",
        project="Permission Intel",
        sync_order=1,
        notes=(
            "Disposable Permission Intel clone; replace only android_permission_intel_dev "
            "from a verified android_permission_intel backup. Production is never modified."
        ),
    ),
    ApprovedSyncPairSpec(
        source="erebus_threat_intel_prod",
        target="erebus_threat_intel_dev",
        project="Erebus",
        sync_order=2,
        depends_on_sources=("android_permission_intel",),
        extra_schema_rewrites={
            "android_permission_intel": "android_permission_intel_dev",
        },
        notes=(
            "Disposable Erebus clone. Approved android_permission_intel identifiers in the "
            "import stream are rewritten to android_permission_intel_dev."
        ),
    ),
    ApprovedSyncPairSpec(
        source="scytaledroid_core_prod",
        target="scytaledroid_core_dev",
        project="ScytaleDroid",
        sync_order=3,
        depends_on_sources=("android_permission_intel",),
        extra_schema_rewrites={
            "android_permission_intel": "android_permission_intel_dev",
        },
        notes=(
            "Disposable ScytaleDroid clone. Approved android_permission_intel identifiers "
            "are rewritten to android_permission_intel_dev where present."
        ),
    ),
)

APPROVED_SYNC_PAIR_BY_SOURCE: dict[str, ApprovedSyncPairSpec] = {
    spec.source: spec for spec in APPROVED_SYNC_PAIRS
}
APPROVED_SYNC_PAIR_BY_TARGET: dict[str, ApprovedSyncPairSpec] = {
    spec.target: spec for spec in APPROVED_SYNC_PAIRS
}


class ProdDevPair(BaseModel):
    prod: str
    expected_dev: str
    dev: str | None = None
    dev_listed: bool = False
    project: str | None = None
    sync_notes: str = ""
    sync_order: int = 0
    depends_on_sources: list[str] = Field(default_factory=list)
    extra_schema_rewrites: dict[str, str] = Field(default_factory=dict)


def approved_pair_for_source(source: str) -> ApprovedSyncPairSpec | None:
    return APPROVED_SYNC_PAIR_BY_SOURCE.get(source)


def approved_pair_for_target(target: str) -> ApprovedSyncPairSpec | None:
    return APPROVED_SYNC_PAIR_BY_TARGET.get(target)


def is_approved_sync_pair(prod_name: str, dev_name: str) -> bool:
    spec = APPROVED_SYNC_PAIR_BY_SOURCE.get(prod_name)
    return spec is not None and spec.target == dev_name


def prod_to_dev_name(prod_name: str) -> str | None:
    """Return the approved disposable clone for a source, if one exists."""
    spec = APPROVED_SYNC_PAIR_BY_SOURCE.get(prod_name)
    if spec is not None:
        return spec.target
    return None


def schema_rewrites_for_pair(source: str, target: str) -> dict[str, str]:
    """Cross-schema identifier rewrites for a prod→dev import stream.

    The original verified backup is never mutated.  These mappings apply only
    to the derived import stream.  Source→target rewrite of the restored
    database itself is handled separately by the importer.
    """
    spec = APPROVED_SYNC_PAIR_BY_SOURCE.get(source)
    if spec is None or spec.target != target:
        return {}
    return dict(spec.extra_schema_rewrites)


def apply_schema_rewrites(names: list[str], rewrites: dict[str, str]) -> list[str]:
    """Map schema names through an approved rewrite table, preserving uniqueness."""
    return sorted({rewrites.get(name, name) for name in names})


def build_prod_dev_pairs(
    database_names: list[str],
    *,
    projects: dict[str, str] | None = None,
) -> list[ProdDevPair]:
    names = set(database_names)
    projects = projects or {}
    pairs: list[ProdDevPair] = []

    for spec in APPROVED_SYNC_PAIRS:
        if spec.source not in names:
            continue
        if not is_in_scope(spec.target):
            continue
        dev_listed = spec.target in names
        notes = (
            spec.notes
            if dev_listed
            else f"Expected dev database '{spec.target}' not in inventory — add to config or catalog."
        )
        pairs.append(
            ProdDevPair(
                prod=spec.source,
                expected_dev=spec.target,
                dev=spec.target if dev_listed else None,
                dev_listed=dev_listed,
                project=spec.project or projects.get(spec.source),
                sync_notes=notes,
                sync_order=spec.sync_order,
                depends_on_sources=list(spec.depends_on_sources),
                extra_schema_rewrites=dict(spec.extra_schema_rewrites),
            )
        )

    return pairs


def order_sync_sources(sources: list[str]) -> list[str]:
    """Stable dependency order for approved sources; unknown names keep input order after."""
    rank = {spec.source: spec.sync_order for spec in APPROVED_SYNC_PAIRS}
    approved = [name for name in sources if name in rank]
    other = [name for name in sources if name not in rank]
    approved.sort(key=lambda name: rank[name])
    return approved + other


def orphan_dev_databases(database_names: list[str], pairs: list[ProdDevPair]) -> list[str]:
    paired_dev = {p.expected_dev for p in pairs if p.dev_listed}
    orphans: list[str] = []
    for name in sorted(database_names):
        if not is_in_scope(name):
            continue
        c = classify_database(name)
        if c.role == DatabaseRole.DEVELOPMENT and name not in paired_dev:
            orphans.append(name)
    return orphans

"""Prod→dev sync readiness based on artifact-verified on-disk backups and freshness."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from mercury.backup.find_latest_backup import (
    BackupSelectionError,
    resolve_backup_directory,
)
from mercury.backup.freshness import (
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    assess_backup_freshness,
    parse_backup_timestamp,
)
from mercury.backup.layout import MANIFEST_FILENAME
from mercury.backup.content_contract import (
    IMPORT_TRANSFORM_VERSION,
    RESTORE_REQUIREMENTS_CONTRACT_VERSION,
    RESTORE_REQUIREMENTS_SCANNER_VERSION,
    RestoreRequirementsContract,
)
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.core.scope import is_in_scope
from mercury.database.discovery import discover_for_planning
from mercury.database.prod_dev_pairs import ProdDevPair, build_prod_dev_pairs


class SyncReadinessEntry(BaseModel):
    prod: str
    expected_dev: str
    dev_listed: bool
    project: str | None = None
    latest_backup_dir: str | None = None
    backup_verified: bool = False
    backup_id: str | None = None
    backup_freshness: str | None = None
    backup_age: str | None = None
    ready_for_sync_planning: bool = False
    blockers: list[str] = Field(default_factory=list)


class RestoreCredentialStatus(BaseModel):
    """Read-only health signal for the dedicated ordinary-dev reset identity."""

    configured: bool = False
    healthy: bool = False
    configured_user: str | None = None
    observed_identity: str | None = None
    detail: str = "[mariadb_restore] not configured"


def assess_restore_credentials(*, probe: bool) -> RestoreCredentialStatus:
    """Inspect the dedicated credential lane without treating it as backup state."""
    from mercury.database.mariadb.client import run_client_query
    from mercury.database.mariadb.config import MariaDbConfigError, load_mariadb_restore_config

    try:
        config = load_mariadb_restore_config()
    except MariaDbConfigError as exc:
        return RestoreCredentialStatus(detail=str(exc))
    status = RestoreCredentialStatus(
        configured=True,
        configured_user=config.user,
        detail=f"Configured · {config.user}",
    )
    if not probe:
        return status
    try:
        observed = run_client_query(
            config, "SELECT CURRENT_USER(), USER(), COALESCE(CURRENT_ROLE(), 'NONE')"
        ).strip().split("\t")
        if len(observed) != 3:
            raise ValueError("restore identity query returned an unexpected result")
    except Exception:
        return status.model_copy(update={"detail": "authentication failed"})
    current_user, _authenticated_user, _role = observed
    if current_user.split("@", 1)[0] != config.user:
        return status.model_copy(update={
            "observed_identity": current_user,
            "detail": "unexpected restore identity",
        })
    return status.model_copy(update={
        "healthy": True,
        "observed_identity": current_user,
        "detail": f"Configured · {current_user}",
    })


def _load_backup_created_at(backup_dir) -> str | None:
    manifest_path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    created_at = data.get("created_at")
    return str(created_at) if created_at else None


def _restore_requirements_issue(backup_dir) -> str | None:
    try:
        data = json.loads((backup_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        contract = RestoreRequirementsContract.model_validate(data.get("restore_requirements"))
    except Exception:
        return "Selected backup predates Mercury's verified restore-requirements contract; development target will not be modified."
    if not contract.verified or contract.issues:
        return "Selected backup restore-requirements contract is not verified."
    if (contract.contract_version != RESTORE_REQUIREMENTS_CONTRACT_VERSION
        or contract.import_transform_version != IMPORT_TRANSFORM_VERSION
        or contract.scanner_version != RESTORE_REQUIREMENTS_SCANNER_VERSION):
        return "Selected backup restore-requirements contract is unsupported by this Mercury importer."
    if contract.artifact_file != data.get("dump_file") or contract.artifact_sha256 != data.get("sha256"):
        return "Selected backup restore-requirements contract does not match the verified dump artifact."
    return None


class SyncReadinessReport(BaseModel):
    mode: str
    backup_root: str
    entries: list[SyncReadinessEntry] = Field(default_factory=list)
    ready_count: int = 0
    blocked_count: int = 0
    restore_credentials: RestoreCredentialStatus = Field(default_factory=RestoreCredentialStatus)


def build_sync_readiness_report(*, live: bool = False) -> SyncReadinessReport:
    """Check prod→dev pairs against verified full backups on disk."""
    from mercury.database.discovery import discover_for_planning

    policy = load_execution_policy()
    inventory = discover_for_planning(live=live)
    mode = "live" if live and inventory.mode == "mariadb_readonly" else "demo"
    names = [entry.name for entry in inventory.entries]
    projects = {entry.name: entry.project for entry in inventory.entries if entry.project}
    pairs = build_prod_dev_pairs(names, projects=projects)

    entries: list[SyncReadinessEntry] = []
    ready_count = 0
    blocked_count = 0
    dumpability_blocked: dict[str, str] = {}
    if live and should_probe_database_status():
        from mercury.backup.dump_preflight import repair_hint, try_assess_sources_dumpability

        prod_names = [
            pair.prod for pair in pairs if is_in_scope(pair.expected_dev)
        ]
        dumpability = try_assess_sources_dumpability(prod_names)
        for entry in dumpability.blocked:
            dumpability_blocked[entry.database] = repair_hint(entry.database)

    for pair in pairs:
        if not is_in_scope(pair.expected_dev):
            continue
        blockers: list[str] = []
        if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
            blockers.append(
                "Backup root is repo-local fallback; configure operator-storage backups before sync readiness."
            )
        if not pair.dev_listed:
            blockers.append(f"Dev target missing: {pair.expected_dev}")
        if pair.prod in dumpability_blocked:
            blockers.append(
                "Production source is not dumpable; "
                + dumpability_blocked[pair.prod]
            )

        backup_dir = None
        try:
            backup_dir = resolve_backup_directory(
                policy.backup_root,
                pair.prod,
                prefer="artifact_verified",
            )
        except BackupSelectionError as exc:
            blockers.append(str(exc))
        backup_verified = False
        backup_id: str | None = None
        backup_freshness: str | None = None
        backup_age: str | None = None
        latest_dir: str | None = None

        if backup_dir is None:
            if not any("No artifact-verified" in b or "No on-disk" in b for b in blockers):
                blockers.append("No artifact-verified on-disk backup found for production source.")
        else:
            latest_dir = str(backup_dir)
            verify = verify_backup_artifacts(backup_dir, database=pair.prod, backup_kind=BACKUP_KIND_FULL)
            backup_verified = verify.verified
            backup_id = verify.backup_id
            created_at = _load_backup_created_at(backup_dir)
            if created_at:
                from mercury.backup.freshness import format_backup_age, parse_backup_timestamp

                backup_age = format_backup_age(parse_backup_timestamp(created_at))
            if not verify.verified:
                blockers.append(
                    f"Pinned backup '{backup_id}' is not artifact-verified (manifest/checksum/size/role)."
                )
            else:
                contract_issue = _restore_requirements_issue(backup_dir)
                if contract_issue:
                    blockers.append(contract_issue)
            if verify.backup_kind != BACKUP_KIND_FULL:
                blockers.append(f"Pinned backup '{backup_id}' is not a verified full backup.")
            elif backup_verified and live and should_probe_database_status():
                freshness = assess_backup_freshness(
                    pair.prod,
                    backup_at=parse_backup_timestamp(_load_backup_created_at(backup_dir)),
                    live=True,
                )
                backup_freshness = freshness.freshness
                if freshness.freshness == FRESHNESS_STALE:
                    blockers.append(
                        "Backup artifacts are artifact-verified but freshness is stale; "
                        f"pinned backup_id={backup_id}."
                    )
                elif freshness.freshness == FRESHNESS_UNKNOWN:
                    blockers.append(
                        "Backup freshness is unknown; cannot approve sync planning "
                        f"for pinned backup_id={backup_id}."
                    )

        ready = pair.dev_listed and backup_verified and not blockers
        if ready:
            ready_count += 1
        else:
            blocked_count += 1

        entries.append(
            SyncReadinessEntry(
                prod=pair.prod,
                expected_dev=pair.expected_dev,
                dev_listed=pair.dev_listed,
                project=pair.project,
                latest_backup_dir=latest_dir,
                backup_verified=backup_verified,
                backup_id=backup_id,
                backup_freshness=backup_freshness,
                backup_age=backup_age,
                ready_for_sync_planning=ready,
                blockers=blockers,
            )
        )

    report = SyncReadinessReport(
        mode=mode,
        backup_root=str(policy.backup_root),
        entries=entries,
        ready_count=ready_count,
        blocked_count=blocked_count,
        restore_credentials=assess_restore_credentials(probe=mode == "live"),
    )
    from mercury.logging.events import log_sync_readiness

    log_sync_readiness(mode=report.mode, ready=report.ready_count, blocked=report.blocked_count)
    return report

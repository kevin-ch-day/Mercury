"""Resolve per-database deployment actions from target state and policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mercury.deploy.models import DeployOptions
from mercury.deploy.safety import planned_import_commands
from mercury.deploy.target_status import TargetStatus, target_status_label

DeployAction = Literal["SKIP", "CREATE_AND_IMPORT", "BLOCKED", "OVERWRITE_DROP"]


@dataclass(frozen=True)
class DeployActionPlan:
    action: DeployAction
    commands: list[str]
    reason: str | None = None


def _exists_status(status: TargetStatus) -> bool:
    return status != "missing"


def _skip_reason_for_status(target_status: TargetStatus, target_database: str) -> str:
    label = target_status_label(target_status)
    return (
        f"SKIP existing database `{target_database}` — "
        f"target status: {label}; skip-existing is enabled"
    )


def resolve_deploy_action(
    *,
    target_database: str,
    dump_path: str,
    target_status: TargetStatus,
    options: DeployOptions,
) -> DeployActionPlan:
    """Never return import commands unless CREATE_AND_IMPORT or explicit OVERWRITE_DROP."""
    exists = _exists_status(target_status)

    if not exists:
        commands, skip_reason = planned_import_commands(
            target_database=target_database,
            dump_path=dump_path,
            options=options,
            exists_on_server=False,
        )
        if skip_reason:
            return DeployActionPlan(action="BLOCKED", commands=[], reason=skip_reason)
        return DeployActionPlan(action="CREATE_AND_IMPORT", commands=commands)

    if options.allow_overwrite_database:
        if not options.allow_drop_database:
            return DeployActionPlan(
                action="BLOCKED",
                commands=[],
                reason=(
                    f"Target database `{target_database}` exists; "
                    "overwrite requires allow_drop_database"
                ),
            )
        commands, skip_reason = planned_import_commands(
            target_database=target_database,
            dump_path=dump_path,
            options=options,
            exists_on_server=True,
        )
        if skip_reason:
            return DeployActionPlan(action="BLOCKED", commands=[], reason=skip_reason)
        return DeployActionPlan(
            action="OVERWRITE_DROP",
            commands=commands,
            reason="explicit overwrite/drop enabled",
        )

    if options.skip_existing:
        verification = {
            "exists_verified": "current DB appears verified",
            "exists_empty": "current DB is empty",
            "exists_unverified": "current DB verification unknown",
            "exists_conflict": "current DB state unknown",
            "exists": "current DB already present",
        }.get(target_status, "current DB already present")
        reason = _skip_reason_for_status(target_status, target_database)
        reason = f"{reason}; verification: {verification}"
        return DeployActionPlan(action="SKIP", commands=[], reason=reason)

    return DeployActionPlan(
        action="BLOCKED",
        commands=[],
        reason=(
            f"Target database `{target_database}` already exists; "
            "default policy will not import into existing protected DBs"
        ),
    )

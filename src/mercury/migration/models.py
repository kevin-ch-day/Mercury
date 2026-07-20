"""Structured, read-only evidence for workstation migration readiness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MigrationCheckState(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    ACTION_NEEDED = "ACTION_NEEDED"
    DECISION_NEEDED = "DECISION_NEEDED"
    NOT_CHECKED = "NOT_CHECKED"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MigrationOverallStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    ACTION_NEEDED = "ACTION_NEEDED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class MigrationCheck:
    id: str
    label: str
    state: MigrationCheckState
    severity: str
    summary: str
    evidence: tuple[str, ...] = ()
    recommended_action: str | None = None
    recommended_command: str | None = None
    blocking: bool = False

    @property
    def unresolved(self) -> bool:
        return self.state not in {MigrationCheckState.PASS, MigrationCheckState.NOT_APPLICABLE}


@dataclass(frozen=True)
class MigrationReadinessReport:
    """Policy state, observed evidence, and operator phase stay intentionally separate."""

    policy_state: str
    observed_mirror: str
    operator_phase: str
    checks: tuple[MigrationCheck, ...] = field(default_factory=tuple)

    @property
    def overall_status(self) -> MigrationOverallStatus:
        if any(check.blocking and check.unresolved for check in self.checks):
            return MigrationOverallStatus.BLOCKED
        if any(
            check.state
            in {
                MigrationCheckState.ACTION_NEEDED,
                MigrationCheckState.DECISION_NEEDED,
                MigrationCheckState.NOT_CHECKED,
            }
            for check in self.checks
        ):
            return MigrationOverallStatus.ACTION_NEEDED
        if any(check.state == MigrationCheckState.WARNING for check in self.checks):
            return MigrationOverallStatus.PASS_WITH_WARNINGS
        return MigrationOverallStatus.PASS

    @property
    def unresolved_checks(self) -> tuple[MigrationCheck, ...]:
        return tuple(check for check in self.checks if check.unresolved)

    def check(self, check_id: str) -> MigrationCheck:
        return next(check for check in self.checks if check.id == check_id)

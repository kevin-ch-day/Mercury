"""Models for fresh-system database deployment from verified operator-storage backups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

PreflightLevel = Literal["ready", "warning", "blocked"]


class PreflightCheck(BaseModel):
    label: str
    level: PreflightLevel
    detail: str = ""
    live_only: bool = False


class DeploymentPreflight(BaseModel):
    hostname: str
    checks: list[PreflightCheck] = Field(default_factory=list)
    existing_databases: list[str] = Field(default_factory=list)
    ready: bool = False

    @property
    def blockers(self) -> list[str]:
        return [c.detail for c in self.checks if c.level == "blocked" and c.detail]

    @property
    def live_blockers(self) -> list[str]:
        return [c.detail for c in self.checks if c.level == "blocked" and c.live_only and c.detail]

    @property
    def planning_blockers(self) -> list[str]:
        return [
            c.detail
            for c in self.checks
            if c.level == "blocked" and not c.live_only and c.detail
        ]

    @property
    def ready_for_planning(self) -> bool:
        return not self.planning_blockers

    @property
    def ready_for_live(self) -> bool:
        return not self.blockers

    @property
    def warnings(self) -> list[str]:
        return [c.detail for c in self.checks if c.level == "warning" and c.detail]


class DeploymentCandidate(BaseModel):
    source_database: str
    target_database: str
    backup_directory: str
    backup_id: str
    dump_path: str
    manifest_path: str
    checksum_path: str
    size_bytes: int = 0
    verified: bool = False
    created_at: str | None = None
    exists_on_server: bool = False
    target_status: str = "missing"
    target_status_detail: str = ""
    table_count: int | None = None
    deploy_action: str = "CREATE_AND_IMPORT"
    action_reason: str | None = None
    skip_reason: str | None = None


class DeploymentPlan(BaseModel):
    mode: str = "dry-run"
    hostname: str
    mariadb_user: str
    execute: bool = False
    candidates: list[DeploymentCandidate] = Field(default_factory=list)
    planned_commands: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    existing_target_policy: str = "skip-existing"
    overwrite_enabled: bool = False
    drop_enabled: bool = False
    deployment_needed: bool = True
    summary_message: str | None = None
    import_count: int = 0
    skip_count: int = 0
    block_count: int = 0

    @property
    def allowed(self) -> bool:
        return not self.blockers and self.import_count > 0


class DeploymentVerification(BaseModel):
    database: str
    exists_on_server: bool = False
    table_count: int | None = None
    verified: bool = False
    detail: str = "basic verification only"
    issues: list[str] = Field(default_factory=list)


class DeploymentExecutionResult(BaseModel):
    source_database: str
    target_database: str
    dry_run: bool = True
    executed: bool = False
    skipped: bool = False
    refused: bool = False
    message: str = ""
    commands: list[str] = Field(default_factory=list)
    verification: DeploymentVerification | None = None


class DeploymentBatchResult(BaseModel):
    mode: str = "dry-run"
    hostname: str
    results: list[DeploymentExecutionResult] = Field(default_factory=list)
    report_path: str | None = None

    @property
    def deployed_count(self) -> int:
        return sum(1 for r in self.results if r.executed)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.skipped)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.refused and not r.skipped)


@dataclass(frozen=True)
class DeployOptions:
    allow_create_database: bool = True
    allow_overwrite_database: bool = False
    allow_drop_database: bool = False
    skip_existing: bool = True

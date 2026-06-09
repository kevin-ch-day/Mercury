"""Models for repository deployment onto a fresh workstation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

RepoDeploySource = Literal["github", "usb_bundle", "none"]


class RepoDeployCandidate(BaseModel):
    key: str
    display_name: str
    target_path: str
    configured_path: str | None = None
    source: RepoDeploySource = "none"
    remote_url: str | None = None
    bundle_path: str | None = None
    branch: str = "main"
    commit: str | None = None
    exists_on_system: bool = False
    skip_reason: str | None = None


class RepoDeployPlan(BaseModel):
    mode: str = "dry-run"
    hostname: str
    source_mode: str = "auto"
    execute: bool = False
    candidates: list[RepoDeployCandidate] = Field(default_factory=list)
    planned_commands: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.blockers and bool(self.candidates)


class RepoDeployResult(BaseModel):
    key: str
    display_name: str
    target_path: str
    dry_run: bool = True
    executed: bool = False
    skipped: bool = False
    refused: bool = False
    message: str = ""
    commands: list[str] = Field(default_factory=list)


class RepoDeployBatchResult(BaseModel):
    mode: str = "dry-run"
    hostname: str
    source_mode: str
    results: list[RepoDeployResult] = Field(default_factory=list)
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
class RepoDeployOptions:
    skip_existing: bool = True
    prefer_usb_bundle: bool = False

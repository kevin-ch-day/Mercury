from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig
from mercury.repo.config import RepoDefinition, RepoSelectionError


def _post_cutover_config(tmp_path: Path) -> StorageConfig:
    primary = tmp_path / "hdd"; legacy = tmp_path / "usb"
    primary.mkdir(); legacy.mkdir()
    return StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "HDD", primary, "hdd", "ext4", True),
        legacy=StorageRootConfig("legacy", StorageRootRole.LEGACY_ARCHIVE, "USB", legacy, "usb", "ext4", False),
        active_write_role=StorageWriteRole.PRIMARY, migration_state=MigrationState.CUTOVER_COMPLETE,
    )


def test_cutover_plan_is_complete_after_cutover(tmp_path: Path) -> None:
    from mercury.storage.cutover_plan import build_cutover_plan
    plan = build_cutover_plan(config=_post_cutover_config(tmp_path))
    assert plan.already_complete is True
    assert plan.runtime_blockers == ()


def test_worktree_selection_rejects_unknown_and_excluded_keys(monkeypatch, tmp_path: Path) -> None:
    from mercury.migration.web_capture import selected_dirty_repositories

    definitions = [RepoDefinition(key="scripts", display_name="Scripts", path=tmp_path / "scripts", migration_scope=False)]
    monkeypatch.setattr("mercury.migration.web_capture.load_repo_definitions", lambda: definitions)
    with pytest.raises(RepoSelectionError, match="Unknown"):
        selected_dirty_repositories(keys={"typo"})
    with pytest.raises(RepoSelectionError, match="excluded"):
        selected_dirty_repositories(keys={"scripts"})

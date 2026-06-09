"""Tests for Mercury repository status, config seeding, and bundle planning."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest

from mercury.repo.bundle import build_repo_bundle_plan, execute_repo_bundle_plan
from mercury.repo.config import (
    RepoBundleSettings,
    RepoDefinition,
    RepoSelectionError,
    render_repo_config,
    select_repo_definitions,
    write_local_repo_config,
)
from mercury.repo.status import inspect_repositories, summarize_repo_statuses


def _git(path: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Mercury Test",
        "GIT_AUTHOR_EMAIL": "mercury@example.com",
        "GIT_COMMITTER_NAME": "Mercury Test",
        "GIT_COMMITTER_EMAIL": "mercury@example.com",
    }
    subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def test_inspect_repositories_reports_dirty_and_untracked(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "mercury")
    (repo / "tracked.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "tracked")
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("u\n", encoding="utf-8")

    statuses = inspect_repositories(
        [RepoDefinition(key="mercury", display_name="Mercury", path=repo)]
    )
    assert len(statuses) == 1
    status = statuses[0]
    assert status.branch == "main"
    assert len(status.commit) == 40
    assert status.dirty is True
    assert status.untracked_count == 1
    assert status.state_label == "dirty"


def test_inspect_repositories_reports_ahead_behind_when_available(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True)
    repo = _make_repo(tmp_path, "erebus")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    (repo / "ahead.txt").write_text("ahead\n", encoding="utf-8")
    _git(repo, "add", "ahead.txt")
    _git(repo, "commit", "-m", "ahead")

    status = inspect_repositories(
        [RepoDefinition(key="erebus", display_name="Erebus", path=repo)]
    )[0]
    assert status.remote_url == str(origin)
    assert status.ahead_count == 1
    assert status.behind_count == 0
    assert status.upstream_label == "+1/-0"


def test_execute_repo_bundle_plan_writes_bundle_manifest_and_runbook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path, "scytaledroid")
    usb_root = tmp_path / "usb"
    repo_root = usb_root / "mercury_repo_backups"
    manifest_dir = usb_root / "mercury_manifests"
    runbook_dir = usb_root / "mercury_runbooks"
    settings = RepoBundleSettings(
        repo_backup_root=repo_root,
        manifest_dir=manifest_dir,
        runbook_dir=runbook_dir,
    )
    statuses = inspect_repositories(
        [RepoDefinition(key="scytaledroid", display_name="ScytaleDroid", path=repo)]
    )
    plan = build_repo_bundle_plan(statuses, settings)
    state_root = tmp_path / "state"

    monkeypatch.setattr("mercury.repo.bundle.REQUIRED_BACKUP_MOUNT", usb_root)
    monkeypatch.setattr(Path, "is_mount", lambda self: self == usb_root)
    monkeypatch.setattr("mercury.state.ledger.resolve_state_root", lambda policy=None: state_root)

    executed = execute_repo_bundle_plan(plan)
    entry = executed.entries[0]
    assert entry.executed is True
    assert entry.planned_bundle_path.exists()
    assert entry.planned_manifest_path.exists()
    assert entry.planned_runbook_path.exists()

    manifest = json.loads(entry.planned_manifest_path.read_text(encoding="utf-8"))
    assert manifest["repo_name"] == "ScytaleDroid"
    assert manifest["bundle_path"] == str(entry.planned_bundle_path)
    assert manifest["bundle_verified"] is True
    assert manifest["bundle_size_bytes"] > 0
    runbook = entry.planned_runbook_path.read_text(encoding="utf-8")
    assert "git clone" in runbook
    assert "Dirty working tree changes and untracked files are not included." in runbook
    index_manifest = json.loads(executed.planned_index_manifest_path.read_text(encoding="utf-8"))
    assert index_manifest["repositories"][0]["repo_name"] == "ScytaleDroid"
    index_runbook = executed.planned_index_runbook_path.read_text(encoding="utf-8")
    assert "Mercury repository transfer runbook" in index_runbook
    repo_csv = (state_root / "repo_bundles.csv").read_text(encoding="utf-8")
    assert "ScytaleDroid" in repo_csv
    operations = (state_root / "operations.jsonl").read_text(encoding="utf-8")
    assert "repo_bundle_written" in operations


def test_execute_repo_bundle_plan_prunes_older_repo_artifacts_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path, "mercury")
    usb_root = tmp_path / "usb"
    repo_root = usb_root / "mercury_repo_backups"
    manifest_dir = usb_root / "mercury_manifests"
    runbook_dir = usb_root / "mercury_runbooks"
    settings = RepoBundleSettings(
        repo_backup_root=repo_root,
        manifest_dir=manifest_dir,
        runbook_dir=runbook_dir,
    )
    statuses = inspect_repositories(
        [RepoDefinition(key="mercury", display_name="Mercury", path=repo)]
    )
    state_root = tmp_path / "state"

    class _FakeDateTime(datetime):
        current = datetime(2026, 6, 9, 3, 18, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.current
            return cls.current.astimezone(tz)

    monkeypatch.setattr("mercury.repo.bundle.datetime", _FakeDateTime)
    monkeypatch.setattr("mercury.repo.bundle.REQUIRED_BACKUP_MOUNT", usb_root)
    monkeypatch.setattr(Path, "is_mount", lambda self: self == usb_root)
    monkeypatch.setattr("mercury.state.ledger.resolve_state_root", lambda policy=None: state_root)

    first_plan = build_repo_bundle_plan(statuses, settings)
    first_executed = execute_repo_bundle_plan(first_plan)
    first_entry = first_executed.entries[0]
    assert first_entry.planned_bundle_path.exists()
    assert first_entry.planned_manifest_path.exists()
    assert first_entry.planned_runbook_path.exists()

    _FakeDateTime.current = datetime(2026, 6, 9, 3, 19, 0, tzinfo=timezone.utc)
    second_plan = build_repo_bundle_plan(statuses, settings)
    second_executed = execute_repo_bundle_plan(second_plan)
    second_entry = second_executed.entries[0]

    assert second_entry.planned_bundle_path.exists()
    assert second_entry.planned_manifest_path.exists()
    assert second_entry.planned_runbook_path.exists()
    assert not first_entry.planned_bundle_path.exists()
    assert not first_entry.planned_manifest_path.exists()
    assert not first_entry.planned_runbook_path.exists()
    assert second_entry.pruned_bundle_paths == [first_entry.planned_bundle_path]
    assert second_entry.pruned_manifest_paths == [first_entry.planned_manifest_path]
    assert second_entry.pruned_runbook_paths == [first_entry.planned_runbook_path]

    operations = (state_root / "operations.jsonl").read_text(encoding="utf-8")
    assert "repo_bundle_retention_pruned" in operations


def test_print_repo_bundle_plan_includes_state_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.repo.bundle import RepoBundleEntry, RepoBundlePlan
    from mercury.repo.terminal import print_repo_bundle_plan
    from mercury.state.summary import StateSummary
    import mercury.repo.terminal as terminal_mod

    terminal_mod.build_state_summary = lambda: StateSummary(
        state_root=tmp_path / "state",
        source="repo-local fallback",
        operations=4,
        database_backup_rows=1,
        repo_bundle_rows=1,
        transfer_package_rows=0,
        sync_event_rows=0,
    )
    plan = RepoBundlePlan(
        generated_at="2026-06-09T00:00:00+00:00",
        repo_backup_root=tmp_path / "usb" / "mercury_repo_backups",
        manifest_dir=tmp_path / "usb" / "mercury_manifests",
        runbook_dir=tmp_path / "usb" / "mercury_runbooks",
        planned_index_manifest_path=tmp_path / "usb" / "mercury_manifests" / "repo_transfer_manifest.json",
        planned_index_runbook_path=tmp_path / "usb" / "mercury_runbooks" / "repo_transfer_runbook.md",
        entries=[
            RepoBundleEntry(
                key="mercury",
                display_name="Mercury",
                repo_path=tmp_path / "Mercury",
                branch="main",
                commit="abc123def456abc123def456abc123def456abcd",
                remote_url="https://example/Mercury.git",
                dirty=False,
                untracked_count=0,
                planned_bundle_path=tmp_path / "usb" / "mercury_repo_backups" / "mercury.bundle",
                planned_manifest_path=tmp_path / "usb" / "mercury_manifests" / "mercury.repo_manifest.json",
                planned_runbook_path=tmp_path / "usb" / "mercury_runbooks" / "mercury.restore.md",
            )
        ],
    )
    print_repo_bundle_plan(plan, executed=False)
    out = capsys.readouterr().out
    assert "State root" in out
    assert "State ops" in out


def test_select_repo_definitions_raises_for_unknown_selection(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "mercury")
    definitions = [RepoDefinition(key="mercury", display_name="Mercury", path=repo)]
    with pytest.raises(RepoSelectionError):
        select_repo_definitions(definitions, selected_keys=["unknown"])


def test_summarize_repo_statuses_counts_states(tmp_path: Path) -> None:
    clean_repo = _make_repo(tmp_path, "clean_repo")
    dirty_repo = _make_repo(tmp_path, "dirty_repo")
    (dirty_repo / "local.txt").write_text("dirty\n", encoding="utf-8")
    statuses = inspect_repositories(
        [
            RepoDefinition(key="clean", display_name="Clean", path=clean_repo),
            RepoDefinition(key="dirty", display_name="Dirty", path=dirty_repo),
        ]
    )
    summary = summarize_repo_statuses(statuses)
    assert summary.configured == 2
    assert summary.clean == 1
    assert summary.dirty == 1
    assert summary.errors == 0


def test_render_repo_config_contains_expected_entries(tmp_path: Path) -> None:
    definitions = [
        RepoDefinition(key="mercury", display_name="Mercury", path=tmp_path / "Mercury"),
        RepoDefinition(key="erebus_engine", display_name="Erebus Engine", path=tmp_path / "erebus"),
    ]
    text = render_repo_config(definitions)
    assert "[repos.mercury]" in text
    assert 'display_name = "Mercury"' in text
    assert f'path = "{tmp_path / "Mercury"}"' in text
    assert "[repos.erebus_engine]" in text


def test_write_local_repo_config_writes_existing_known_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mercury_repo = tmp_path / "Mercury"
    scripts_repo = tmp_path / "fedora-linux-scripts"
    mercury_repo.mkdir()
    scripts_repo.mkdir()
    monkeypatch.setattr(
        "mercury.repo.config.DEFAULT_LOCAL_REPO_CANDIDATES",
        [
            ("mercury", "Mercury", str(mercury_repo)),
            ("fedora_linux_scripts", "Fedora Linux Scripts", str(scripts_repo)),
            ("missing_repo", "Missing Repo", str(tmp_path / "missing")),
        ],
    )

    destination = tmp_path / "config" / "repos.toml"
    written_path, definitions = write_local_repo_config(path=destination)
    assert written_path == destination
    assert destination.exists()
    assert [definition.key for definition in definitions] == ["mercury", "fedora_linux_scripts"]
    text = destination.read_text(encoding="utf-8")
    assert "[repos.mercury]" in text
    assert "[repos.fedora_linux_scripts]" in text
    assert "missing_repo" not in text

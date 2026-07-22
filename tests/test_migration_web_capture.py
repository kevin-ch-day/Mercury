from __future__ import annotations

import json
from pathlib import Path
import subprocess

from mercury.repo.config import RepoDefinition


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _repo(tmp_path: Path) -> tuple[Path, RepoDefinition]:
    repo = tmp_path / "erebus-web"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Mercury test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "binary.bin").write_bytes(b"\x00base\x01")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo, RepoDefinition(key="erebus_web", display_name="Erebus Web", path=repo)


def _capture(monkeypatch, tmp_path: Path, definition: RepoDefinition):
    from mercury.migration import web_capture

    operator = tmp_path / "operator"
    operator.mkdir()
    monkeypatch.setattr(web_capture, "resolve_operator_mount", lambda: operator)
    monkeypatch.setattr(
        web_capture, "assert_operator_storage_path", lambda _path, **_kwargs: None
    )
    results = web_capture.capture_web_worktrees(execute=True, repositories=[definition])
    assert results[0].error is None
    assert results[0].restore_checked is True
    return results[0].snapshot_dir


def test_capture_dirty_tracked_change(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    assert b"changed" in (snapshot / "tracked-unstaged.patch").read_bytes()
    manifest = json.loads((snapshot / "snapshot_manifest.json").read_text())
    assert manifest["restore_validation"]["passed"] is True


def test_capture_staged_change(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    assert b"staged" in (snapshot / "staged.patch").read_bytes()


def test_capture_binary_patch(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / "binary.bin").write_bytes(b"\x00changed\xff")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    assert b"GIT binary patch" in (snapshot / "tracked-unstaged.patch").read_bytes()


def test_capture_ignored_files_only_in_inventory(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / ".gitignore").write_text("secret.env\n", encoding="utf-8")
    _git(repo, "add", ".gitignore"); _git(repo, "commit", "-m", "ignore secret")
    (repo / "secret.env").write_text("not copied", encoding="utf-8")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    manifest = json.loads((snapshot / "snapshot_manifest.json").read_text())
    assert "secret.env" in manifest["ignored_files"]
    import tarfile
    with tarfile.open(snapshot / "untracked-files.tar.gz") as archive:
        assert "secret.env" not in archive.getnames()


def test_capture_excludes_untracked_runtime_secret_contents(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / ".env").write_text("DATABASE_PASSWORD=not-for-archive\n", encoding="utf-8")
    (repo / "notes.txt").write_text("safe worktree note\n", encoding="utf-8")

    snapshot = _capture(monkeypatch, tmp_path, definition)
    manifest = json.loads((snapshot / "snapshot_manifest.json").read_text())
    assert ".env" in manifest["excluded_sensitive_untracked"]
    assert ".env" not in manifest["untracked_files"]

    import tarfile
    with tarfile.open(snapshot / "untracked-files.tar.gz") as archive:
        assert ".env" not in archive.getnames()
        assert "notes.txt" in archive.getnames()


def test_capture_worktrees_accepts_non_web_configured_repo(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    definition = RepoDefinition(key="mercury", display_name="Mercury", path=repo)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    from mercury.migration import web_capture
    operator = tmp_path / "operator"; operator.mkdir()
    monkeypatch.setattr(web_capture, "resolve_operator_mount", lambda: operator)
    monkeypatch.setattr(
        web_capture, "assert_operator_storage_path", lambda _path, **_kwargs: None
    )
    result = web_capture.capture_worktrees(execute=True, repositories=[definition])
    assert result[0].key == "mercury"
    assert result[0].restore_checked is True

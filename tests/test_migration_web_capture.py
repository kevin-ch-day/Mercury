from __future__ import annotations

import json
import hashlib
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


def _capture(monkeypatch, tmp_path: Path, definition: RepoDefinition,
             reviewed_source_exceptions: dict[str, str] | None = None):
    from mercury.migration import web_capture

    operator = tmp_path / "operator"
    operator.mkdir()
    monkeypatch.setattr(web_capture, "resolve_operator_mount", lambda: operator)
    monkeypatch.setattr(
        web_capture, "assert_operator_storage_path", lambda _path, **_kwargs: None
    )
    results = web_capture.capture_web_worktrees(
        execute=True, repositories=[definition],
        reviewed_source_exceptions=reviewed_source_exceptions)
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


def test_capture_reconstructs_staged_then_unstaged_change(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    from mercury.migration.web_capture import validate_snapshot

    assert validate_snapshot(snapshot) is True


def test_capture_reconstructs_intent_to_add_files(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "new file.txt").write_text("first\n", encoding="utf-8")
    (repo / "another.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "-N", "--", "new file.txt", "another.txt")

    snapshot = _capture(monkeypatch, tmp_path, definition)
    from mercury.migration.web_capture import validate_snapshot

    manifest = json.loads((snapshot / "snapshot_manifest.json").read_text())
    assert manifest["restore_validation"]["passed"] is True
    assert manifest["untracked_files"] == []
    assert validate_snapshot(snapshot) is True


def test_capture_refuses_modified_untracked_archive(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / "notes.txt").write_text("original\n", encoding="utf-8")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    archive = snapshot / "untracked-files.tar.gz"
    with archive.open("ab") as handle:
        handle.write(b"corruption")
    from mercury.migration.web_capture import validate_snapshot

    assert validate_snapshot(snapshot) is False


def test_capture_reports_failed_restore_validation(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    from mercury.migration import web_capture

    operator = tmp_path / "operator"
    operator.mkdir()
    monkeypatch.setattr(web_capture, "resolve_operator_mount", lambda: operator)
    monkeypatch.setattr(web_capture, "assert_operator_storage_path", lambda _path, **_kwargs: None)
    monkeypatch.setattr(web_capture, "validate_snapshot", lambda _path: False)

    result = web_capture.capture_worktrees(execute=True, repositories=[definition])[0]
    assert result.restore_checked is False
    assert result.error == "RESTORE_VALIDATION_FAILED"


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
    from mercury.migration.web_capture import snapshot_status
    assert snapshot_status(definition) == ("current", True)
    (repo / ".env").write_text("DATABASE_PASSWORD=changed-but-excluded\n", encoding="utf-8")
    assert snapshot_status(definition) == ("current", True)


def test_reviewed_source_exception_is_exact_and_visible(monkeypatch, tmp_path: Path) -> None:
    import tarfile
    from mercury.migration.web_capture import snapshot_status, validate_snapshot

    repo, definition = _repo(tmp_path)
    source = repo / "tests" / "static_analysis" / "test_secret_family_semantics.py"
    source.parent.mkdir(parents=True)
    source.write_text("def test_family():\n    assert True\n", encoding="utf-8")
    (repo / ".env").write_text("PASSWORD=synthetic\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    from mercury.migration.web_capture import _capture_data
    default_manifest, *_ = _capture_data(definition)
    relative = source.relative_to(repo).as_posix()
    assert relative in default_manifest["excluded_sensitive_untracked"]
    assert default_manifest["reviewed_source_exceptions"] == {}
    from mercury.migration import web_capture
    preview = web_capture.capture_web_worktrees(execute=False, repositories=[definition])[0]
    assert preview.excluded_sensitive_count == 2

    reviewed = _capture(monkeypatch, tmp_path, definition, {relative: digest})
    manifest = json.loads((reviewed / "snapshot_manifest.json").read_text())
    assert manifest["reviewed_source_exceptions"] == {relative: digest}
    assert relative in manifest["untracked_files"]
    assert relative not in manifest["excluded_sensitive_untracked"]
    assert ".env" in manifest["excluded_sensitive_untracked"]
    with tarfile.open(reviewed / "untracked-files.tar.gz") as archive:
        assert relative in archive.getnames()
        assert ".env" not in archive.getnames()
    assert validate_snapshot(reviewed)
    assert snapshot_status(definition) == ("current", True)
    source.write_text("def test_family():\n    assert False\n", encoding="utf-8")
    assert snapshot_status(definition) == ("invalid", False)


def test_reviewed_source_exception_rejects_runtime_and_mismatched_bytes(monkeypatch, tmp_path: Path) -> None:
    from mercury.migration import web_capture

    repo, definition = _repo(tmp_path)
    source = repo / "tests" / "test_secret.py"
    source.parent.mkdir()
    source.write_text("assert True\n", encoding="utf-8")
    (repo / ".env").write_text("synthetic\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (repo / "config").mkdir()
    (repo / "config" / "test_secret.py").write_text("synthetic\n", encoding="utf-8")
    for relative, expected in [("tests/test_secret.py", "0" * 64),
                               (".env", hashlib.sha256((repo / ".env").read_bytes()).hexdigest()),
                               ("tests/missing_secret.py", digest),
                               ("config/test_secret.py", hashlib.sha256((repo / "config" / "test_secret.py").read_bytes()).hexdigest())]:
        result = web_capture.capture_web_worktrees(
            execute=False, repositories=[definition],
            reviewed_source_exceptions={relative: expected})[0]
        assert result.error and "EXCEPTION" in result.error


def test_reviewed_source_exception_rejects_symlinks_and_malformed_receipts(monkeypatch, tmp_path: Path) -> None:
    from mercury.migration import web_capture

    repo, definition = _repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "config").mkdir()
    secret = repo / "config" / "test_secret.py"
    secret.write_text("synthetic\n", encoding="utf-8")
    (repo / "tests" / "test_secret.py").symlink_to(secret)
    digest = hashlib.sha256(secret.read_bytes()).hexdigest()
    assert not web_capture._reviewed_source_exception(repo, "tests/test_secret.py", digest)
    (repo / "tests" / "test_secret.py").unlink()
    (repo / "tests" / "linked").symlink_to(repo / "config", target_is_directory=True)
    assert not web_capture._reviewed_source_exception(repo, "tests/linked/test_secret.py", digest)
    assert not web_capture._reviewed_source_exception(repo, "tests/../config/test_secret.py", digest)
    assert not web_capture._reviewed_source_exception(repo, "tests/test_secret.py", 123)

    operator = tmp_path / "operator"
    manifest = operator / "mercury_worktree_snapshots" / "x" / repo.name / "snapshot_manifest.json"
    manifest.parent.mkdir(parents=True)
    monkeypatch.setattr(web_capture, "resolve_operator_mount", lambda: operator)
    for malformed in (["invalid"], [], None, ""):
        manifest.write_text(json.dumps({"reviewed_source_exceptions": malformed,
                                        "status_fingerprint": "fake", "restore_validation": {"passed": True}}))
        assert web_capture.snapshot_status(definition) == ("invalid", False)


def test_reviewed_source_cli_requires_one_repo_and_passes_exact_binding(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from mercury.cli import app
    from mercury.migration import web_capture

    calls = []
    monkeypatch.setattr(web_capture, "capture_worktrees", lambda **kwargs: calls.append(kwargs) or [])
    digest = "a" * 64
    args = ["migration", "capture-worktrees", "--reviewed-source", f"tests/test_secret.py={digest}"]
    assert CliRunner().invoke(app, args).exit_code != 0
    result = CliRunner().invoke(app, [*args, "--repo", "scytaledroid"])
    assert result.exit_code == 0
    assert calls == [{"execute": False, "keys": {"scytaledroid"},
                      "reviewed_source_exceptions": {"tests/test_secret.py": digest}}]


def test_untracked_content_and_membership_change_snapshot_freshness(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    note = repo / "notes.txt"
    note.write_text("first\n", encoding="utf-8")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    from mercury.migration.web_capture import snapshot_status, validate_snapshot
    assert snapshot_status(definition) == ("current", True)
    assert validate_snapshot(snapshot)
    note.write_text("second\n", encoding="utf-8")
    assert snapshot_status(definition) == ("stale", True)
    note.write_text("first\n", encoding="utf-8")
    assert snapshot_status(definition) == ("current", True)
    extra = repo / "extra.txt"
    extra.write_text("extra\n", encoding="utf-8")
    assert snapshot_status(definition) == ("stale", True)
    extra.unlink()
    assert snapshot_status(definition) == ("current", True)
    note.unlink()
    assert snapshot_status(definition) == ("stale", True)


def test_untracked_archive_bytes_match_manifest(monkeypatch, tmp_path: Path) -> None:
    import hashlib
    import tarfile
    repo, definition = _repo(tmp_path)
    (repo / "notes.txt").write_bytes(b"captured source bytes\n")
    snapshot = _capture(monkeypatch, tmp_path, definition)
    manifest = json.loads((snapshot / "snapshot_manifest.json").read_text())
    with tarfile.open(snapshot / "untracked-files.tar.gz") as archive:
        contents = archive.extractfile("notes.txt").read()
    assert contents == (repo / "notes.txt").read_bytes()
    assert manifest["untracked_sha256"]["notes.txt"] == hashlib.sha256(contents).hexdigest()


def test_oversized_untracked_capture_fails_closed(monkeypatch, tmp_path: Path) -> None:
    repo, definition = _repo(tmp_path)
    from mercury.migration import web_capture
    (repo / "large.bin").write_bytes(b"123456789")
    monkeypatch.setattr(web_capture, "MAX_UNTRACKED_FINGERPRINT_BYTES", 8)
    assert web_capture.snapshot_status(definition) == ("missing", False)
    result = web_capture.capture_web_worktrees(execute=False, repositories=[definition])[0]
    assert result.error == "UNTRACKED_FINGERPRINT_LIMIT"


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


def test_reviewed_source_hash_is_bound_to_exact_file(tmp_path):
    from mercury.migration.web_capture import _capture_data
    repo, definition = _repo(tmp_path)
    (repo / "tests").mkdir()
    first = repo / "tests/test_secret_one.py"
    second = repo / "tests/test_secret_two.py"
    first.write_text("assert True")
    second.write_text("assert False")
    import pytest
    with pytest.raises(ValueError, match="INVALID_REVIEWED_SOURCE_EXCEPTION"):
        _capture_data(definition, reviewed_source_exceptions={
            "tests/test_secret_two.py": hashlib.sha256(first.read_bytes()).hexdigest()})


def test_reviewed_source_rechecks_bytes_at_archive(tmp_path):
    import pytest
    import tarfile
    from mercury.migration.web_capture import _safe_tar_add
    repo, _ = _repo(tmp_path)
    (repo / "tests").mkdir()
    source = repo / "tests/test_secret.py"
    source.write_text("assert True")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text("assert False")
    with tarfile.open(tmp_path / "archive.tar", "w") as archive:
        with pytest.raises(ValueError, match="REVIEWED_SOURCE_CHANGED"):
            _safe_tar_add(archive, repo, "tests/test_secret.py", expected_sha256=digest)
        assert archive.getmembers() == []


def test_reviewed_exception_keeps_storage_write_guards(monkeypatch, tmp_path):
    import pytest
    from mercury.migration import web_capture
    from mercury.core import usb_mount
    from mercury.storage import host_maintenance
    repo, definition = _repo(tmp_path)
    (repo / "tests").mkdir()
    source = repo / "tests/test_secret.py"
    source.write_text("assert True")
    exceptions = {"tests/test_secret.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    operator = tmp_path / "operator"
    operator.mkdir()
    monkeypatch.setattr(web_capture, "resolve_operator_mount", lambda: operator)
    monkeypatch.setattr(usb_mount, "resolve_operator_mount", lambda: operator)
    monkeypatch.setattr(usb_mount, "usb_mount_is_active", lambda _: False)
    results = web_capture.capture_web_worktrees(execute=True, repositories=[definition],
                                                reviewed_source_exceptions=exceptions)
    assert "not active" in results[0].error
    assert list(operator.iterdir()) == []
    monkeypatch.setattr(usb_mount, "usb_mount_is_active", lambda _: True)
    def refuse(_):
        raise RuntimeError("writes disabled for safe disconnect")
    monkeypatch.setattr(host_maintenance, "refuse_if_hdd_writes_disabled", refuse)
    with pytest.raises(RuntimeError, match="safe disconnect"):
        web_capture.capture_web_worktrees(execute=True, repositories=[definition],
                                         reviewed_source_exceptions=exceptions)
    assert list(operator.iterdir()) == []

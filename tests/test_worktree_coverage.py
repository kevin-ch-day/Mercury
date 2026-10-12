"""Read-only registered-worktree coverage checks."""

from pathlib import Path
import subprocess

from mercury.repo.worktree_coverage import audit_registered_worktrees


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_dirty_and_unpublished_worktrees_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "base")

    dirty = tmp_path / "dirty"
    _git(root, "worktree", "add", "-qb", "review", str(dirty))
    (dirty / "notes.txt").write_text("untracked\n", encoding="utf-8")
    clean_unique = tmp_path / "clean-unique"
    _git(root, "worktree", "add", "-qb", "unique", str(clean_unique))
    (clean_unique / "tracked.txt").write_text("unique\n", encoding="utf-8")
    _git(clean_unique, "commit", "-qam", "unique")

    rows = audit_registered_worktrees(root, configured_paths={root, dirty})
    by_path = {row.path: row for row in rows}
    assert by_path[root].requires_preservation_review is False
    assert by_path[dirty].dirty is True
    assert by_path[dirty].configured_for_capture is True
    assert by_path[dirty].requires_preservation_review is True
    assert by_path[dirty].missing_capture_mapping is False
    assert by_path[clean_unique].dirty is False
    assert by_path[clean_unique].head_reachable_from_main is False
    assert by_path[clean_unique].missing_capture_mapping is True


def test_missing_registered_worktree_remains_a_review_item(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "base")
    missing = tmp_path / "missing"
    _git(root, "worktree", "add", "-qb", "temp", str(missing))
    (missing / "tracked.txt").unlink()
    (missing / ".git").unlink()
    missing.rmdir()

    rows = audit_registered_worktrees(root, configured_paths={root})
    row = next(row for row in rows if row.path == missing)
    assert row.exists is False
    assert row.dirty is None
    assert row.requires_preservation_review is True
    assert row.missing_capture_mapping is True

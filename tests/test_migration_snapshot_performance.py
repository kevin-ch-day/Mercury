from __future__ import annotations

from pathlib import Path

from mercury.repo.config import RepoDefinition


def test_snapshot_status_skips_recursive_runtime_metadata(monkeypatch, tmp_path: Path) -> None:
    from mercury.migration import web_capture

    repo = RepoDefinition(key="mercury", display_name="Mercury", path=tmp_path / "Mercury")
    root = tmp_path / "operator" / "mercury_worktree_snapshots" / "x" / "Mercury"
    root.mkdir(parents=True)
    (root / "snapshot_manifest.json").write_text('{"status_fingerprint":"same", "restore_validation":{"passed":true}}')
    monkeypatch.setattr(web_capture, "resolve_operator_mount", lambda: tmp_path / "operator")
    monkeypatch.setattr(web_capture, "_capture_data", lambda _repo, *, include_runtime_metadata=True: ({"status_fingerprint": "same"}, b"", b"", [], []))
    assert web_capture.snapshot_status(repo) == ("current", True)

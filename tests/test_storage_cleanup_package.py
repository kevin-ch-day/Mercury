"""Tests for retention policy, cleanup preview, and destination package allowlist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.migration.destination_package import preview_destination_package
from mercury.storage.cleanup import (
    CleanupClassification,
    build_cleanup_preview,
    build_cleanup_status,
    classify_top_level,
    refuse_cleanup_execute,
)
from mercury.storage.retention import RetentionPolicy, load_retention_policy


def _policy(**kwargs) -> RetentionPolicy:
    base = load_retention_policy()
    data = base.__dict__.copy()
    data.update(kwargs)
    data["source_path"] = None
    return RetentionPolicy(**data)


def test_scytaledroid_roots_never_automatic_cleanup_candidates() -> None:
    policy = _policy()
    for name in (
        "scytaledroid_migration_checkpoints",
        "scytaledroid_apk_store_backups",
        "scytaledroid_artifacts",
    ):
        assert classify_top_level(name, policy=policy) == CleanupClassification.MANUAL_REVIEW_ONLY


def test_scytaledroid_excluded_from_destination_by_default(tmp_path: Path) -> None:
    policy = _policy()
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=policy,
    )
    for name in (
        "scytaledroid_migration_checkpoints",
        "scytaledroid_apk_store_backups",
        "scytaledroid_artifacts",
    ):
        assert name in report.excluded_top_level


def test_scytaledroid_inclusion_requires_flag_and_paths(tmp_path: Path) -> None:
    policy = _policy(allow_scytaledroid_in_destination=False)
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=policy,
        allow_scytaledroid=True,
        scytaledroid_paths=[],
    )
    assert any("exact path list" in err for err in report.errors)


def test_phase3b_run_and_backup_ids_protected(tmp_path: Path) -> None:
    policy = _policy()
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    (phase / "PHASE3B_REPORT.md").write_text("sealed\n", encoding="utf-8")
    preview = build_cleanup_preview(tmp_path, policy=policy)
    protected_paths = [
        e.path for e in preview.entries if e.classification == CleanupClassification.PROTECTED
    ]
    assert any("20260722T055400Z_phase3b" in path for path in protected_paths)
    assert policy.protects_backup_id(
        "erebus_threat_intel_prod-full-20260722_055507_238"
    )
    assert policy.protects_backup_id(
        "android_permission_intel-full-20260722_055648_287"
    )


def test_erebus_capture_and_historical_worktrees_protected(tmp_path: Path) -> None:
    policy = _policy()
    capture = (
        tmp_path
        / ".mercury_control"
        / "validation"
        / "erebus"
        / "erebus_destination_candidate_3f1bb5b_20260722T150930Z"
    )
    capture.mkdir(parents=True)
    (capture / "note.txt").write_text("ok\n", encoding="utf-8")
    for stamp in ("20260722T055310Z", "20260722T055352Z"):
        root = tmp_path / "mercury_worktree_snapshots" / stamp
        root.mkdir(parents=True)
        (root / "manifest.json").write_text("{}", encoding="utf-8")
    preview = build_cleanup_preview(tmp_path, policy=policy)
    kinds = {e.reason for e in preview.entries if e.classification == CleanupClassification.PROTECTED}
    assert "protected_capture_id" in kinds
    assert "historical_phase3b_worktree_capture" in kinds


def test_package_rejects_unqualified_latest(tmp_path: Path) -> None:
    report = preview_destination_package(
        tmp_path,
        run_id="latest",
        policy=_policy(),
    )
    assert report.uses_unqualified_latest
    assert report.ok is False


def test_package_preview_lists_includes_and_excludes(tmp_path: Path) -> None:
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    (phase / "a.json").write_text("{}", encoding="utf-8")
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=_policy(),
    )
    assert report.excluded_top_level
    assert any(m.kind == "phase3b_run" for m in report.included)
    assert any(m.kind == "document" for m in report.included)


def test_missing_exact_backup_id_fails(tmp_path: Path) -> None:
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=_policy(),
    )
    assert any("missing exact backup id" in err for err in report.errors)
    assert report.ok is False


def test_referenced_phase3b_not_cleanup_candidate(tmp_path: Path) -> None:
    policy = _policy()
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    preview = build_cleanup_preview(tmp_path, policy=policy)
    for entry in preview.entries:
        if "20260722T055400Z_phase3b" in entry.path:
            assert entry.classification == CleanupClassification.PROTECTED


def test_destination_validation_pending_disables_execute() -> None:
    policy = _policy(destination_validation_pending=True, allow_execute=True)
    assert policy.cleanup_execute_allowed() is False
    assert "destination_validation_pending" in refuse_cleanup_execute(policy)


def test_cleanup_status_read_only(tmp_path: Path) -> None:
    before = {p.name for p in tmp_path.iterdir()} if tmp_path.exists() else set()
    report = build_cleanup_status(tmp_path, policy=_policy())
    after = {p.name for p in tmp_path.iterdir()} if tmp_path.exists() else set()
    assert before == after
    assert report.cleanup_execution_state == "refused"


def test_preview_writes_plan_only_when_requested(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    report = build_cleanup_preview(tmp_path, policy=_policy(), write_plan_path=plan)
    assert plan.is_file()
    assert report.plan_written == str(plan)
    payload = json.loads(plan.read_text(encoding="utf-8"))
    assert "entries" in payload


def test_project_roots_outside_governance_are_manual_review() -> None:
    assert (
        classify_top_level("obsidiandroid_release_artifacts", policy=_policy())
        == CleanupClassification.MANUAL_REVIEW_ONLY
    )


def test_dev_backup_candidate_only_after_retention_minimum(tmp_path: Path) -> None:
    policy = _policy(destination_validation_pending=True, development_keep_latest_verified=2)
    backups = tmp_path / "mercury_backups" / "2026-07-22" / "erebus_threat_intel_dev"
    for stamp in ("a", "b", "c"):
        path = backups / stamp
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(
            json.dumps(
                {
                    "backup_id": f"erebus_threat_intel_dev-full-{stamp}",
                    "database": "erebus_threat_intel_dev",
                    "created_at": "2026-07-22T16:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
    preview = build_cleanup_preview(tmp_path, policy=policy)
    candidates = [
        e
        for e in preview.entries
        if e.classification == CleanupClassification.CLEANUP_CANDIDATE_AFTER_DESTINATION
    ]
    assert candidates
    assert all("execute refused" in e.reason for e in candidates)


def test_only_valid_production_backup_protected_by_policy() -> None:
    policy = _policy(
        protected_backup_ids=("erebus_threat_intel_prod-full-20260722_055507_238",)
    )
    assert policy.protects_backup_id("erebus_threat_intel_prod-full-20260722_055507_238")
    assert not policy.protects_backup_id("erebus_threat_intel_prod-full-OTHER")


def test_package_size_excludes_scytaledroid(tmp_path: Path) -> None:
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    (phase / "x.bin").write_bytes(b"abc")
    scytale = tmp_path / "scytaledroid_artifacts" / "apk"
    scytale.mkdir(parents=True)
    (scytale / "huge.apk").write_bytes(b"x" * 10_000)
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=_policy(),
    )
    assert report.estimated_size_bytes < 10_000
    assert "scytaledroid_artifacts" in report.excluded_top_level


def test_erebus_intake_subset_excludes_downloads(tmp_path: Path) -> None:
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    intake = tmp_path / "erebus-intake"
    (intake / "downloads").mkdir(parents=True)
    (intake / "downloads" / "big.bin").write_bytes(b"x" * 5000)
    (intake / "archive").mkdir(parents=True)
    (intake / "manifests").mkdir(parents=True)
    (intake / "manifests" / "a.yaml").write_text("k: v\n", encoding="utf-8")
    (intake / "prepared").mkdir(parents=True)
    (intake / "ingest_ready").mkdir(parents=True)
    (intake / "notes").mkdir(parents=True)
    (intake / "intake_contract.json").write_text('{"layout_version":1}\n', encoding="utf-8")
    (intake / "README.md").write_text("readme\n", encoding="utf-8")
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=_policy(),
    )
    assert "downloads" in report.intake_excluded
    assert "erebus-intake/downloads" in report.excluded_top_level
    assert not any("downloads" in m.path for m in report.included)
    assert any(m.kind == "erebus_intake_contract" for m in report.included)
    assert report.estimated_size_bytes < 5000


def test_path_traversal_and_symlink_escape_rejected(tmp_path: Path) -> None:
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    outside = tmp_path.parent / "outside_escape"
    outside.mkdir(exist_ok=True)
    (outside / "secret").write_text("nope", encoding="utf-8")
    link = tmp_path / "scytaledroid_artifacts"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not permitted")
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=_policy(),
        allow_scytaledroid=True,
        scytaledroid_paths=["scytaledroid_artifacts"],
    )
    assert any("escape" in err.lower() or "symlink" in err.lower() for err in report.errors)


def test_normal_preview_does_not_require_scytaledroid_deep_audit(tmp_path: Path) -> None:
    # Presence of a giant fake APK tree must not be walked for hashing during preview.
    scytale = tmp_path / "scytaledroid_migration_checkpoints" / "data" / "sha256" / "aa"
    scytale.mkdir(parents=True)
    (scytale / "aa.apk").write_bytes(b"0" * 1024)
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=_policy(),
    )
    assert "scytaledroid_migration_checkpoints" in report.excluded_top_level
    assert all("scytaledroid" not in m.path for m in report.included if m.kind != "document")


def test_historical_vs_current_mercury_identities_distinguished() -> None:
    policy = _policy(
        historical_phase3b_mercury_commit="40b8f532ff2b49e9cdd699d4af01e88dde9aa8c0",
        current_destination_mercury_commit="",
    )
    assert policy.historical_phase3b_mercury_commit.startswith("40b8f532")
    assert policy.current_destination_mercury_commit == ""

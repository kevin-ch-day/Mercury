"""Tests for destination planning document generation and validation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mercury.core.storage_roles import DEFAULT_PRIMARY_UUID
from mercury.migration.destination_documents import (
    DOCUMENT_IDS,
    DOCUMENT_SCHEMA,
    UNRESOLVED,
    _atomic_write_json,
    _assert_no_secret_values,
    _assert_scope_safe,
    classify_unresolved_fields,
    evaluate_package_create_preconditions,
    generate_destination_documents,
    legacy_documents_dir,
    load_destination_documents,
    required_evidence_errors,
    validate_documents_against_preview_pins,
    verify_document_payload_checksum,
)
from mercury.migration.destination_package import preview_destination_package
from mercury.storage.retention import RetentionPolicy, load_retention_policy


def _policy(**kwargs) -> RetentionPolicy:
    base = load_retention_policy()
    data = base.__dict__.copy()
    data.update(kwargs)
    data["source_path"] = None
    return RetentionPolicy(**data)


def _seed_evidence(tmp_path: Path) -> None:
    phase = tmp_path / ".mercury_control" / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    (phase / "phase3b_summary.json").write_text(
        json.dumps(
            {
                "run_id": "20260722T055400Z_phase3b",
                "host": "testhost",
                "readiness_status": "READY",
                "destination_cutover_started": False,
                "zero_unexplained_restore_differences": True,
                "restore_schemas_retained": [
                    "_restorecheck_erebus_threat_intel_prod_20260722T055400Z_phase3b"
                ],
            }
        ),
        encoding="utf-8",
    )
    (phase / "preflight").mkdir()
    (phase / "preflight" / "preflight.json").write_text(
        json.dumps({"mariadb_version": "10.11.18-MariaDB", "df_h": "ok"}),
        encoding="utf-8",
    )
    (phase / "dumps").mkdir()
    (phase / "dumps" / "dump_metadata.json").write_text(
        json.dumps(
            {
                "dumps": {
                    "erebus_threat_intel_prod": {
                        "directory": str(tmp_path / "b1"),
                        "manifest": {
                            "backup_id": "erebus_threat_intel_prod-full-20260722_055507_238",
                            "dump_file": "e.sql.gz",
                            "schema_file": "e.schema.sql.gz",
                            "sha256": "a" * 64,
                            "schema_sha256": "b" * 64,
                            "size_bytes": 1,
                        },
                    },
                    "android_permission_intel": {
                        "directory": str(tmp_path / "b2"),
                        "manifest": {
                            "backup_id": "android_permission_intel-full-20260722_055648_287",
                            "dump_file": "a.sql.gz",
                            "schema_file": "a.schema.sql.gz",
                            "sha256": "c" * 64,
                            "schema_sha256": "d" * 64,
                            "size_bytes": 1,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (phase / "restore").mkdir()
    (phase / "restore" / "source_vs_restore_comparison.json").write_text(
        "{}\n", encoding="utf-8"
    )
    merc = (
        tmp_path
        / ".mercury_control"
        / "validation"
        / "mercury"
        / "mercury_destination_candidate_2596b85_20260722T180435Z"
    )
    merc.mkdir(parents=True)
    (merc / "capture_identity.json").write_text(
        json.dumps(
            {
                "capture_id": "mercury_destination_candidate_2596b85_20260722T180435Z",
                "commit": "2596b8588c868a68d661dfaae23a5609cc77279a",
                "tree": "6c5fd49394384a532ac0320119d1fcbd8c4a52a6",
                "repository_url": "https://github.com/kevin-ch-day/Mercury.git",
                "branch": "main",
            }
        ),
        encoding="utf-8",
    )
    ereb = (
        tmp_path
        / ".mercury_control"
        / "validation"
        / "erebus"
        / "erebus_destination_candidate_3f1bb5b_20260722T150930Z"
    )
    ereb.mkdir(parents=True)
    (ereb / "capture_summary.json").write_text(
        json.dumps(
            {
                "repository": {
                    "url": "https://github.com/kevin-ch-day/erebus-engine-fedora.git",
                    "branch": "main",
                    "tree": "796f180621b938dcc7415d0bfaba8daa71a4e43f",
                },
                "intake_contract": {"sha256": "e" * 64},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".mercury_control" / "storage_identity.json").write_text(
        json.dumps(
            {
                "filesystem_uuid": DEFAULT_PRIMARY_UUID,
                "filesystem_label": "MERCURY_DATA_V2",
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def mount_ok(monkeypatch: pytest.MonkeyPatch):
    import mercury.migration.destination_documents as docs_mod

    class _Ok:
        ok = True
        blocker = None
        code = "ok"

    monkeypatch.setattr(docs_mod, "validate_storage_mount", lambda **kwargs: _Ok())


def test_all_four_documents_generated(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    result = generate_destination_documents(
        tmp_path, documents_run="test_run_1", overwrite_legacy_documents=False
    )
    assert result.ok, result.errors
    assert {d.document_id for d in result.documents} == set(DOCUMENT_IDS)
    for doc in result.documents:
        assert doc.payload["schema"] == DOCUMENT_SCHEMA
        assert doc.path.is_file()
        assert oct(doc.path.stat().st_mode & 0o777) == "0o600"


def test_missing_required_evidence_fails_closed(tmp_path: Path, mount_ok) -> None:
    result = generate_destination_documents(tmp_path, documents_run="missing")
    assert result.ok is False
    assert any("required evidence missing" in e for e in result.errors)


def test_backup_id_mismatch_fails(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    result = generate_destination_documents(tmp_path, documents_run="bak")
    docs = {d.document_id: d for d in result.documents}
    errors = validate_documents_against_preview_pins(
        docs,
        run_id="20260722T055400Z_phase3b",
        mercury_commit="2596b8588c868a68d661dfaae23a5609cc77279a",
        mercury_capture_id="mercury_destination_candidate_2596b85_20260722T180435Z",
        erebus_commit="3f1bb5bd2229d98b9b76b9f1615238792f12a0b3",
        protected_backup_ids=("wrong-backup-id",),
    )
    assert any("missing backup id" in e for e in errors)


def test_git_commit_capture_mismatch_fails(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    result = generate_destination_documents(tmp_path, documents_run="git")
    docs = {d.document_id: d for d in result.documents}
    errors = validate_documents_against_preview_pins(
        docs,
        run_id="20260722T055400Z_phase3b",
        mercury_commit="0" * 40,
        mercury_capture_id="wrong_capture",
        erebus_commit="1" * 40,
        protected_backup_ids=_policy().protected_backup_ids,
    )
    assert any("mercury_commit mismatch" in e for e in errors)
    assert any("mercury_capture_id mismatch" in e for e in errors)


def test_secret_looking_values_rejected() -> None:
    payload = {
        "body": {"note": "password: hunter2hunter2"},
    }
    assert _assert_no_secret_values(payload)


def test_unresolved_operator_fields_remain_placeholders(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    result = generate_destination_documents(tmp_path, documents_run="unres")
    assert result.ok
    total = sum(d.unresolved_field_count for d in result.documents)
    assert total > 0
    text = json.dumps([d.payload for d in result.documents])
    assert UNRESOLVED in text
    # No silent destination mount assumption
    inv = next(d for d in result.documents if d.document_id == "source_host_inventory")
    mount = inv.payload["body"]["destination_system_requirements"]["filesystem_and_mounts"][
        "destination_mount_path"
    ]
    assert mount == UNRESOLVED


def test_documents_agree_on_pins(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    result = generate_destination_documents(tmp_path, documents_run="agree")
    docs = {d.document_id: d for d in result.documents}
    errors = validate_documents_against_preview_pins(
        docs,
        run_id="20260722T055400Z_phase3b",
        mercury_commit="2596b8588c868a68d661dfaae23a5609cc77279a",
        mercury_capture_id="mercury_destination_candidate_2596b85_20260722T180435Z",
        erebus_commit="3f1bb5bd2229d98b9b76b9f1615238792f12a0b3",
        protected_backup_ids=_policy().protected_backup_ids,
    )
    assert errors == []
    commits = {d.payload["mercury_commit"] for d in result.documents}
    assert len(commits) == 1


def test_scytaledroid_cannot_become_required_content() -> None:
    payload = {
        "body": {
            "note": "must include scytaledroid_artifacts as package member",
        }
    }
    assert _assert_scope_safe(payload)


def test_atomic_write_failure_leaves_no_partial_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "doc.json"
    payload = {"schema": DOCUMENT_SCHEMA, "sha256": "", "body": {"x": 1}}

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        _atomic_write_json(target, payload)
    assert not target.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_inactive_mount_refuses_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mercury.migration.destination_documents as docs_mod

    class _Bad:
        ok = False
        blocker = "wrong_uuid"
        code = "wrong_uuid"

    monkeypatch.setattr(docs_mod, "validate_storage_mount", lambda **kwargs: _Bad())
    _seed_evidence(tmp_path)
    result = generate_destination_documents(tmp_path, documents_run="inactive")
    assert result.ok is False
    assert result.mount_uuid_ok is False
    assert not (tmp_path / ".mercury_control" / "destination").exists() or not any(
        (tmp_path / ".mercury_control" / "destination").rglob("source_host_inventory.json")
    )


def test_checksum_failure_blocks_preview_approval(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    result = generate_destination_documents(
        tmp_path, overwrite_legacy_documents=True, documents_run="legacy"
    )
    assert result.ok
    path = legacy_documents_dir(tmp_path, "20260722T055400Z_phase3b") / (
        "source_host_inventory.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sha256"] = "0" * 64
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert verify_document_payload_checksum(payload)
    loaded = load_destination_documents(tmp_path, "20260722T055400Z_phase3b")
    # Tampered document omitted → incomplete set
    assert "source_host_inventory" not in loaded
    report = preview_destination_package(
        tmp_path,
        run_id="20260722T055400Z_phase3b",
        policy=_policy(
            current_destination_mercury_commit="2596b8588c868a68d661dfaae23a5609cc77279a",
            current_destination_mercury_capture_id="mercury_destination_candidate_2596b85_20260722T180435Z",
            current_erebus_destination_commit="3f1bb5bd2229d98b9b76b9f1615238792f12a0b3",
        ),
        mercury_commit="2596b8588c868a68d661dfaae23a5609cc77279a",
        mercury_capture_id="mercury_destination_candidate_2596b85_20260722T180435Z",
    )
    assert any(u.startswith("document:") for u in report.unresolved)


def test_package_create_preconditions_refuse() -> None:
    refusals = evaluate_package_create_preconditions(
        preview_id=None,
        preview_checksum=None,
        expected_preview_checksum="abc",
        source_artifacts_unchanged=False,
        members_match_preview=False,
        uses_unqualified_latest=True,
        protected_checksum_ok=False,
        scytale_or_obsidian_present=True,
        active_hdd_identity_ok=False,
        documents={},
    )
    assert "preview ID is missing" in refusals
    assert "an unqualified latest appears" in refusals
    assert "Scytale or Obsidian project data appears" in refusals


def test_classify_unresolved_has_no_package_blockers(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    result = generate_destination_documents(tmp_path, documents_run="cls")
    docs = {d.document_id: d for d in result.documents}
    rows = classify_unresolved_fields(docs)
    assert rows
    assert all(r["required_before_package_create"] == "no" for r in rows)
    assert all(r["class"] != "PACKAGE_CREATION_BLOCKER" for r in rows)


def test_new_run_does_not_overwrite_legacy(tmp_path: Path, mount_ok) -> None:
    _seed_evidence(tmp_path)
    first = generate_destination_documents(tmp_path, overwrite_legacy_documents=True)
    assert first.ok
    legacy_path = legacy_documents_dir(tmp_path, "20260722T055400Z_phase3b") / (
        "source_host_inventory.json"
    )
    before = legacy_path.read_bytes()
    second = generate_destination_documents(tmp_path, documents_run="second")
    assert second.ok
    assert legacy_path.read_bytes() == before
    assert "documents_runs" in str(second.documents_dir)

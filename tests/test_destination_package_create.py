"""Fail-closed destination package create tests (temporary fixtures only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.core.storage_roles import CONTROL_DIRNAME, DEFAULT_PRIMARY_UUID
from mercury.migration.destination_documents import (
    DOCUMENT_IDS,
    DOCUMENT_SCHEMA,
    DOCUMENT_SCHEMA_VERSION,
    UNRESOLVED,
    _atomic_write_json,
)
from mercury.migration.destination_package_create import (
    CREATE_CONFIRMATION,
    create_destination_package,
    packages_root,
)
from mercury.migration.destination_package_seal import (
    compute_preview_sha256,
    fingerprint_member,
    load_sealed_preview,
)
from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    mark_detached,
    save_host_maintenance,
)


RUN_ID = "20260722T055400Z_phase3b"
MERCURY_COMMIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MERCURY_CAPTURE = "mercury_destination_candidate_aaaaaaaa_20260722T000000Z"
EREBUS_COMMIT = "3f1bb5bd2229d98b9b76b9f1615238792f12a0b3"
EREBUS_CAPTURE = "erebus_destination_candidate_3f1bb5b_20260722T150930Z"
BACKUP_IDS = frozenset(
    {
        "erebus_threat_intel_prod-full-20260722_055507_238",
        "android_permission_intel-full-20260722_055648_287",
    }
)
PREVIEW_ID = "preview_20260722T055400Z_phase3b_20260722T000001Z"


@pytest.fixture
def host_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    save_host_maintenance(HostMaintenanceState(), path=path)
    return path


@pytest.fixture
def mount_ok(monkeypatch: pytest.MonkeyPatch):
    import mercury.migration.destination_package_create as create_mod

    class _Ok:
        ok = True
        blocker = None
        code = "ok"

    monkeypatch.setattr(
        create_mod, "validate_storage_mount", lambda **kwargs: _Ok()
    )
    monkeypatch.setattr(Path, "is_mount", lambda self: True)


def _write_docs(mount: Path, *, blocker_field: bool = False) -> None:
    docs_dir = (
        mount
        / CONTROL_DIRNAME
        / "destination"
        / RUN_ID
        / "documents_runs"
        / "fixture_docs"
    )
    docs_dir.mkdir(parents=True)
    for doc_id in DOCUMENT_IDS:
        body: dict = {"note": "fixture"}
        if doc_id == "source_host_inventory":
            body = {
                "destination_system_requirements": {
                    "filesystem_and_mounts": {
                        "destination_mount_path": UNRESOLVED,
                    }
                }
            }
            if blocker_field:
                body["package_create_blocker_field"] = UNRESOLVED
        payload = {
            "schema": DOCUMENT_SCHEMA,
            "schema_version": DOCUMENT_SCHEMA_VERSION,
            "document_id": doc_id,
            "filename": f"{doc_id}.json",
            "generated_at_utc": "2026-07-22T00:00:00Z",
            "source_run_id": RUN_ID,
            "linked_preview_id": PREVIEW_ID,
            "mercury_commit": MERCURY_COMMIT,
            "mercury_capture_id": MERCURY_CAPTURE,
            "erebus_commit": EREBUS_COMMIT,
            "erebus_capture_id": EREBUS_CAPTURE,
            "sha256": "",
            "unresolved_field_count": 1 if doc_id == "source_host_inventory" else 0,
            "generated_by": {"tool": "test"},
            "evidence_refs": [],
            "body": body,
        }
        _atomic_write_json(docs_dir / f"{doc_id}.json", payload)


def _seal_preview(
    mount: Path,
    members: list[dict],
    *,
    preview_id: str = PREVIEW_ID,
    ok: bool = True,
    uses_latest: bool = False,
    fingerprints: list[dict] | None = None,
) -> dict:
    payload = {
        "schema": "mercury.destination_package_preview.v1",
        "preview_id": preview_id,
        "run_id": RUN_ID,
        "generated_at": "2026-07-22T00:00:00Z",
        "mount_root": str(mount),
        "ok": ok,
        "errors": [],
        "unresolved": [],
        "uses_unqualified_latest": uses_latest,
        "estimated_size_bytes": sum(m.get("size_bytes", 0) for m in members),
        "file_count": len(members),
        "manifest_reference_count": 0,
        "included_backup_ids": sorted(BACKUP_IDS),
        "included_capture_ids": [MERCURY_CAPTURE, EREBUS_CAPTURE],
        "included_git_commits": [
            f"current_destination_mercury_commit={MERCURY_COMMIT}",
            f"erebus_commit={EREBUS_COMMIT}",
        ],
        "intake_included": [
            "intake_contract.json",
            "README.md",
            "manifests",
            "ingest_ready",
            "prepared",
            "notes",
        ],
        "intake_excluded": ["downloads", "archive", "logs", "tools"],
        "excluded_top_level": sorted(
            [
                "scytaledroid_migration_checkpoints",
                "scytaledroid_apk_store_backups",
                "scytaledroid_artifacts",
                "mercury_repo_clones",
            ]
        ),
        "included": members,
        "mercury_commit": MERCURY_COMMIT,
        "mercury_capture_id": MERCURY_CAPTURE,
        "sealed_at_utc": "2026-07-22T00:00:01Z",
        "member_fingerprints": fingerprints or [],
    }
    if fingerprints is None:
        fps = []
        for member in members:
            path = Path(member["path"])
            if path.exists():
                fps.append(fingerprint_member(path, mount))
        payload["member_fingerprints"] = fps
    payload["preview_sha256"] = compute_preview_sha256(payload)
    out = (
        mount
        / CONTROL_DIRNAME
        / "destination"
        / RUN_ID
        / "previews"
        / preview_id
    )
    out.mkdir(parents=True)
    (out / "preview.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "PREVIEW.sha256").write_text(payload["preview_sha256"] + "\n", encoding="utf-8")
    return payload


def _member(path: Path, kind: str, identity: str) -> dict:
    return {
        "path": str(path),
        "kind": kind,
        "identity": identity,
        "mode": "copy",
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "required": True,
    }


def _seed_ok_layout(tmp_path: Path) -> tuple[Path, list[dict]]:
    mount = tmp_path / "mnt"
    mount.mkdir()
    evidence = mount / "evidence" / "phase.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("phase3b-evidence\n", encoding="utf-8")
    capture = mount / "captures" / "mercury.txt"
    capture.parent.mkdir(parents=True)
    capture.write_text("mercury-capture\n", encoding="utf-8")
    members = [
        _member(evidence, "phase3b_evidence", "phase.txt"),
        _member(capture, "mercury_capture", "mercury.txt"),
    ]
    _write_docs(mount)
    _seal_preview(mount, members)
    return mount, members


def _create(mount: Path, **kwargs):
    defaults = dict(
        preview_id=PREVIEW_ID,
        run_id=RUN_ID,
        confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT,
        mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=EREBUS_COMMIT,
        erebus_capture_id=EREBUS_CAPTURE,
        expected_backup_ids=BACKUP_IDS,
        verify_git_head=False,
        package_id="destination_rehearsal_fixture_pkg",
    )
    defaults.update(kwargs)
    return create_destination_package(mount, **defaults)


def test_exact_preview_id_required(tmp_path: Path, host_state, mount_ok) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    result = _create(mount, preview_id="")
    assert not result.ok
    assert any("exact preview ID" in e for e in result.errors)


def test_preview_checksum_mismatch_refuses(tmp_path: Path, host_state, mount_ok) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    path = (
        mount
        / CONTROL_DIRNAME
        / "destination"
        / RUN_ID
        / "previews"
        / PREVIEW_ID
        / "preview.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["preview_sha256"] = "0" * 64
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = _create(mount)
    assert not result.ok
    assert any("checksum" in e.lower() for e in result.errors)


def test_changed_source_artifact_refuses(tmp_path: Path, host_state, mount_ok) -> None:
    mount, members = _seed_ok_layout(tmp_path)
    Path(members[0]["path"]).write_text("changed\n", encoding="utf-8")
    result = _create(mount)
    assert not result.ok
    assert any("changed since preview" in e for e in result.errors)


def test_missing_artifact_refuses(tmp_path: Path, host_state, mount_ok) -> None:
    mount, members = _seed_ok_layout(tmp_path)
    Path(members[0]["path"]).unlink()
    result = _create(mount)
    assert not result.ok
    assert any("missing" in e.lower() for e in result.errors)


def test_extra_member_refuses(tmp_path: Path, host_state, mount_ok, monkeypatch) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    import mercury.migration.destination_package_create as create_mod

    original = create_mod._copy_member

    def _copy_with_extra(source, dest_root, *, mount_root, logical_name):
        files, size, errors = original(
            source, dest_root, mount_root=mount_root, logical_name=logical_name
        )
        sneaky = dest_root / "ZZZ_unexpected_extra"
        sneaky.write_text("extra\n", encoding="utf-8")
        return files, size, errors

    monkeypatch.setattr(create_mod, "_copy_member", _copy_with_extra)
    result = _create(mount)
    assert not result.ok
    assert any("unexpected members" in e for e in result.errors)
    assert not (packages_root(mount) / "destination_rehearsal_fixture_pkg").exists()


def test_scytaledroid_inclusion_refuses(tmp_path: Path, host_state, mount_ok) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    scytale = mount / "scytaledroid_artifacts" / "x.bin"
    scytale.parent.mkdir(parents=True)
    scytale.write_bytes(b"x")
    members = [_member(scytale, "scytaledroid_approved", "scytaledroid_artifacts/x.bin")]
    _write_docs(mount)
    _seal_preview(mount, members)
    result = _create(mount)
    assert not result.ok
    assert any("Scytale" in e for e in result.errors)


def test_excluded_obsidian_inclusion_refuses(tmp_path: Path, host_state, mount_ok) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    obs = mount / "obsidiandroid_core_accounts" / "x.bin"
    obs.parent.mkdir(parents=True)
    obs.write_bytes(b"x")
    members = [_member(obs, "obsidian", "obsidiandroid_core_accounts/x.bin")]
    _write_docs(mount)
    _seal_preview(mount, members)
    result = _create(mount)
    assert not result.ok
    assert any("Obsidian" in e for e in result.errors)


def test_unqualified_latest_refuses(tmp_path: Path, host_state, mount_ok) -> None:
    mount, members = _seed_ok_layout(tmp_path)
    # Rewrite sealed preview with uses_unqualified_latest
    path = (
        mount
        / CONTROL_DIRNAME
        / "destination"
        / RUN_ID
        / "previews"
        / PREVIEW_ID
        / "preview.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["uses_unqualified_latest"] = True
    data["preview_sha256"] = compute_preview_sha256(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (
        mount
        / CONTROL_DIRNAME
        / "destination"
        / RUN_ID
        / "previews"
        / PREVIEW_ID
        / "PREVIEW.sha256"
    ).write_text(data["preview_sha256"] + "\n", encoding="utf-8")
    result = _create(mount)
    assert not result.ok
    assert any("latest" in e.lower() for e in result.errors)


def test_preview_id_with_latest_refuses(tmp_path: Path, host_state, mount_ok) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    result = _create(mount, preview_id="preview_latest")
    assert not result.ok


def test_bad_hdd_uuid_refuses(tmp_path: Path, host_state, monkeypatch) -> None:
    import mercury.migration.destination_package_create as create_mod

    class _Bad:
        ok = False
        blocker = "UUID mismatch"
        code = "uuid_mismatch"

    monkeypatch.setattr(
        create_mod, "validate_storage_mount", lambda **kwargs: _Bad()
    )
    monkeypatch.setattr(Path, "is_mount", lambda self: True)
    mount, _ = _seed_ok_layout(tmp_path)
    result = _create(mount)
    assert not result.ok
    assert any("UUID" in e or "HDD" in e or "uuid" in e.lower() for e in result.errors)


def test_inactive_mount_refuses(tmp_path: Path, host_state, monkeypatch) -> None:
    import mercury.migration.destination_package_create as create_mod

    class _Ok:
        ok = True
        blocker = None
        code = "ok"

    monkeypatch.setattr(
        create_mod, "validate_storage_mount", lambda **kwargs: _Ok()
    )
    monkeypatch.setattr(Path, "is_mount", lambda self: False)
    mount, _ = _seed_ok_layout(tmp_path)
    result = _create(mount)
    assert not result.ok
    assert any("host-shadow" in e for e in result.errors)


def test_secret_value_detection_refuses(
    tmp_path: Path, host_state, mount_ok, monkeypatch
) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    import mercury.migration.destination_package_create as create_mod

    original_reject = create_mod._reject_secret_text

    def _always_secret(text: str):
        return ["possible secret value embedded in package metadata"]

    # Patch after copy by making inventory secret check fail — inject into
    # _reject_secret_text during verification of control JSON.
    monkeypatch.setattr(create_mod, "_reject_secret_text", _always_secret)
    result = _create(mount)
    assert not result.ok
    assert any("secret" in e.lower() for e in result.errors)
    assert not (packages_root(mount) / "destination_rehearsal_fixture_pkg").exists()
    # partial may remain
    assert result.verification_status == "DESTINATION_PACKAGE_FAILED"
    del original_reject


def test_atomic_incomplete_package_not_exposed(
    tmp_path: Path, host_state, mount_ok, monkeypatch
) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    import mercury.migration.destination_package_create as create_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated copy failure")

    monkeypatch.setattr(create_mod, "_copy_member", _boom)
    result = _create(mount)
    assert not result.ok
    final = packages_root(mount) / "destination_rehearsal_fixture_pkg"
    assert not final.exists()


def test_successful_package_membership_and_sha256(
    tmp_path: Path, host_state, mount_ok
) -> None:
    mount, members = _seed_ok_layout(tmp_path)
    evidence_before = Path(members[0]["path"]).read_text(encoding="utf-8")
    result = _create(mount)
    assert result.ok, result.errors
    assert result.verification_status == "DESTINATION_PACKAGE_VERIFIED"
    assert result.package_root is not None
    assert result.package_root.is_dir()
    assert (result.package_root / "package_manifest.json").is_file()
    assert (result.package_root / "package_file_inventory.json").is_file()
    assert (result.package_root / "package_members.sha256").is_file()
    assert (result.package_root / "package_receipt.json").is_file()
    assert (result.package_root / "source_preview.json").is_file()
    assert (result.package_root / "verification_report.json").is_file()
    for doc_id in DOCUMENT_IDS:
        assert (result.package_root / "destination_documents" / f"{doc_id}.json").is_file()

    receipt = json.loads(
        (result.package_root / "package_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["mercury_commit"] == MERCURY_COMMIT
    assert receipt["mercury_capture_id"] == MERCURY_CAPTURE
    assert receipt["erebus_capture_id"] == EREBUS_CAPTURE
    assert set(receipt["backup_ids"]) == BACKUP_IDS
    assert receipt["verification_status"] == "DESTINATION_PACKAGE_VERIFIED"

    # SHA lines verify
    for line in (result.package_root / "package_members.sha256").read_text(
        encoding="utf-8"
    ).splitlines():
        digest, rel = line.split("  ", 1)
        import hashlib

        data = (result.package_root / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest

    manifest = json.loads(
        (result.package_root / "package_manifest.json").read_text(encoding="utf-8")
    )
    preview_idents = {(m["kind"], m["identity"]) for m in members}
    pkg_idents = {(m["kind"], m["identity"]) for m in manifest["members"]}
    assert preview_idents == pkg_idents

    # destination-only unresolved preserved
    unresolved = receipt.get("unresolved_operator_inputs") or []
    assert any(row.get("class") == "DESTINATION_PREP_REQUIRED" for row in unresolved)

    # source evidence unchanged
    assert Path(members[0]["path"]).read_text(encoding="utf-8") == evidence_before


def test_destination_only_unresolved_preserved(
    tmp_path: Path, host_state, mount_ok
) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    result = _create(mount)
    assert result.ok
    receipt = json.loads(
        (result.package_root / "package_receipt.json").read_text(encoding="utf-8")
    )
    assert any(
        r.get("required_before_package_create") == "no" for r in receipt["unresolved_operator_inputs"]
    )


def test_package_creation_blocker_refuses(
    tmp_path: Path, host_state, mount_ok, monkeypatch
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    evidence = mount / "e.txt"
    evidence.write_text("e\n", encoding="utf-8")
    members = [_member(evidence, "phase3b_evidence", "e.txt")]
    _write_docs(mount)
    _seal_preview(mount, members)

    def _blocker_rows(docs):
        return [
            {
                "field": "forced.blocker",
                "document": "source_host_inventory",
                "class": "PACKAGE_CREATION_BLOCKER",
                "required_before_package_create": "yes",
                "destination_step": "now",
                "who_supplies": "operator",
            }
        ]

    import mercury.migration.destination_documents as docs_mod
    import mercury.migration.destination_package_create as create_mod

    monkeypatch.setattr(docs_mod, "classify_unresolved_fields", _blocker_rows)
    monkeypatch.setattr(create_mod, "classify_unresolved_fields", _blocker_rows)
    result = _create(mount)
    assert not result.ok
    assert any("package-creation-blocking" in e for e in result.errors)


def test_non_interactive_requires_confirmation(
    tmp_path: Path, host_state, mount_ok
) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    result = _create(mount, confirm="WRONG")
    assert not result.ok
    assert any("confirmation" in e for e in result.errors)


def test_repeated_create_same_package_id_refuses(
    tmp_path: Path, host_state, mount_ok
) -> None:
    mount, _ = _seed_ok_layout(tmp_path)
    first = _create(mount)
    assert first.ok
    second = _create(mount)
    assert not second.ok
    assert any("already exists" in e for e in second.errors)


def test_host_detached_refuses_create(tmp_path: Path, host_state, mount_ok) -> None:
    mark_detached(path=host_state)
    mount, _ = _seed_ok_layout(tmp_path)
    result = _create(mount)
    assert not result.ok
    assert any("host maintenance" in e for e in result.errors)


def test_load_sealed_preview_rejects_latest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="latest"):
        load_sealed_preview(tmp_path, run_id=RUN_ID, preview_id="latest_preview")

"""Backup eligibility must bind the actual declared artifacts, before cleanup."""

import hashlib
import json
from pathlib import Path

import pytest

from mercury.backup.checksum import write_checksum_file
from mercury.backup.verification import verify_backup_artifacts


def seed(root: Path) -> dict:
    (root / "dump.sql.gz").write_bytes(b"original dump")
    (root / "schema.sql.gz").write_bytes(b"original schema")
    (root / "other.txt").write_bytes(b"unrelated")
    manifest = {
        "backup_id": "inert-binding-fixture",
        "database": "scytaledroid_core_prod",
        "backup_kind": "full",
        "created_at": "2026-10-30T00:00:00+00:00",
        "dump_file": "dump.sql.gz",
        "schema_file": "schema.sql.gz",
        "sha256": hashlib.sha256(b"original dump").hexdigest(),
        "schema_sha256": hashlib.sha256(b"original schema").hexdigest(),
        "size_bytes": 13,
        "schema_size_bytes": 15,
        "source_role": "production",
        "live_actions_enabled": True,
        "dry_run": False,
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    write_checksum_file(root, ["dump.sql.gz", "schema.sql.gz"])
    return manifest


def test_exact_artifact_binding_and_legacy_optional_metadata(tmp_path):
    manifest = seed(tmp_path)
    good = verify_backup_artifacts(tmp_path)
    assert good.verified and good.verification_method == "manifest-artifact-binding-v2"
    for field in ["sha256", "schema_sha256", "size_bytes", "schema_size_bytes"]:
        manifest.pop(field)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert verify_backup_artifacts(tmp_path).verified  # checksums still cover both files


def test_manifest_source_role_must_match_database_classification(tmp_path):
    manifest = seed(tmp_path)
    manifest["source_role"] = "development"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = verify_backup_artifacts(tmp_path, database="scytaledroid_core_prod")
    assert not result.verified
    assert any("source role" in issue for issue in result.issues)


def test_manifest_requested_and_authenticated_users_must_match(tmp_path):
    manifest = seed(tmp_path)
    manifest.update(requested_user="backup_reader", authenticated_user="other@localhost")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = verify_backup_artifacts(tmp_path, database="scytaledroid_core_prod")
    assert not result.verified
    assert any("authenticated MariaDB users differ" in issue for issue in result.issues)


@pytest.mark.parametrize("defect", [
    "omitted_dump", "omitted_schema", "manifest_digest", "manifest_size",
    "schema_digest", "schema_size", "duplicate_checksum", "malformed_checksum",
    "checksum_traversal", "manifest_traversal", "absolute_manifest", "symlink_dump",
    "symlink_manifest", "symlink_checksum",
])
def test_bad_backup_never_qualifies(tmp_path, defect):
    manifest = seed(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside.sql.gz")
    outside.write_bytes(b"original dump")
    if defect == "omitted_dump":
        write_checksum_file(tmp_path, ["other.txt", "schema.sql.gz"])
    elif defect == "omitted_schema":
        write_checksum_file(tmp_path, ["dump.sql.gz"])
    elif defect in {"manifest_digest", "schema_digest"}:
        manifest["sha256" if defect == "manifest_digest" else "schema_sha256"] = "0" * 64
    elif defect in {"manifest_size", "schema_size"}:
        manifest["size_bytes" if defect == "manifest_size" else "schema_size_bytes"] = 999
    elif defect == "duplicate_checksum":
        sidecar = tmp_path / "checksum.sha256"
        sidecar.write_text(sidecar.read_text() * 2)
    elif defect == "malformed_checksum":
        with (tmp_path / "checksum.sha256").open("a") as out:
            out.write("bad unparseable entry\n")
    elif defect == "checksum_traversal":
        (tmp_path / "checksum.sha256").write_text(manifest["sha256"] + "  ../" + outside.name + "\n")
    elif defect in {"manifest_traversal", "absolute_manifest"}:
        manifest["dump_file"] = "../" + outside.name if defect == "manifest_traversal" else str(outside)
    elif defect == "symlink_dump":
        (tmp_path / "dump.sql.gz").unlink()
        (tmp_path / "dump.sql.gz").symlink_to(outside)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    if defect in {"symlink_manifest", "symlink_checksum"}:
        path = tmp_path / ("manifest.json" if defect == "symlink_manifest" else "checksum.sha256")
        original = tmp_path / ("original-" + path.name)
        path.rename(original)
        path.symlink_to(original)
    result = verify_backup_artifacts(tmp_path)
    assert not result.verified, result
    assert outside.read_bytes() == b"original dump"


def test_requested_kind_cannot_override_manifest(tmp_path):
    seed(tmp_path)
    result = verify_backup_artifacts(tmp_path, backup_kind="schema_only")
    assert not result.verified


def test_schema_only_requires_its_declared_file_in_checksum(tmp_path):
    manifest = seed(tmp_path)
    manifest.update(backup_kind="schema_only", schema_file=None)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    write_checksum_file(tmp_path, ["other.txt"])
    assert not verify_backup_artifacts(tmp_path).verified


def test_gnu_filename_with_spaces_is_not_truncated(tmp_path):
    from mercury.backup.checksum import verify_checksums
    (tmp_path / "name with spaces.sql.gz").write_bytes(b"inert")
    sidecar = write_checksum_file(tmp_path, ["name with spaces.sql.gz"])
    assert verify_checksums(tmp_path, sidecar) == (True, [])

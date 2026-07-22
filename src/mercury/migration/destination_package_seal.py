"""Seal and load governed destination-package previews (exact ID, no 'latest')."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from mercury.core.storage_roles import CONTROL_DIRNAME, DEFAULT_PRIMARY_UUID
from mercury.core.storage_validate import validate_storage_mount
from mercury.migration.destination_package import (
    DestinationPackagePreview,
    PackageMember,
    preview_destination_package,
)
from mercury.storage.retention import RetentionPolicy, load_retention_policy

PREVIEW_SCHEMA = "mercury.destination_package_preview.v1"


def previews_root(mount_root: Path, run_id: str) -> Path:
    return mount_root / CONTROL_DIRNAME / "destination" / run_id / "previews"


def preview_dir(mount_root: Path, run_id: str, preview_id: str) -> Path:
    if "latest" in preview_id.lower():
        raise ValueError("preview ID must not contain unqualified 'latest'")
    return previews_root(mount_root, run_id) / preview_id


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chmod_restrictive(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _chmod_restrictive(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def preview_to_dict(report: DestinationPackagePreview) -> dict[str, Any]:
    return {
        "schema": PREVIEW_SCHEMA,
        "preview_id": report.preview_id,
        "run_id": report.run_id,
        "generated_at": report.generated_at,
        "mount_root": report.mount_root,
        "ok": report.ok,
        "errors": list(report.errors),
        "unresolved": list(report.unresolved),
        "uses_unqualified_latest": report.uses_unqualified_latest,
        "estimated_size_bytes": report.estimated_size_bytes,
        "file_count": report.file_count,
        "manifest_reference_count": report.manifest_reference_count,
        "included_backup_ids": list(report.included_backup_ids),
        "included_capture_ids": list(report.included_capture_ids),
        "included_git_commits": list(report.included_git_commits),
        "intake_included": list(report.intake_included),
        "intake_excluded": list(report.intake_excluded),
        "excluded_top_level": list(report.excluded_top_level),
        "included": [asdict(m) for m in report.included],
    }


def canonical_preview_bytes(payload: dict[str, Any]) -> bytes:
    body = dict(payload)
    body.pop("preview_sha256", None)
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")


def compute_preview_sha256(payload: dict[str, Any]) -> str:
    return _sha256_bytes(canonical_preview_bytes(payload))


def fingerprint_member(path: Path, mount_root: Path) -> dict[str, Any]:
    """Record change-detection fingerprint for a preview member path."""
    resolved = path.resolve()
    root = mount_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"member escapes mount root: {path}")
    if path.is_symlink():
        target = path.resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"symlink escape: {path}")
    if not path.exists():
        return {"path": str(path), "missing": True}
    if path.is_file():
        return {
            "path": str(path),
            "type": "file",
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "mtime_ns": path.stat().st_mtime_ns,
        }
    files = 0
    size = 0
    digest = hashlib.sha256()
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in sorted(filenames):
            fp = Path(dirpath) / name
            if fp.is_symlink():
                continue
            if not fp.is_file():
                continue
            files += 1
            try:
                st = fp.stat()
                size += st.st_size
                rel = str(fp.relative_to(path)).encode()
                digest.update(rel)
                digest.update(str(st.st_size).encode())
                digest.update(_sha256_file(fp).encode())
            except OSError:
                continue
    return {
        "path": str(path),
        "type": "dir",
        "file_count": files,
        "size_bytes": size,
        "tree_fingerprint": digest.hexdigest(),
    }


def seal_destination_package_preview(
    mount_root: Path,
    *,
    run_id: str,
    mercury_commit: str,
    mercury_capture_id: str,
    policy: RetentionPolicy | None = None,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    preview_id: str | None = None,
) -> dict[str, Any]:
    """Build and seal a governed preview with full membership and checksum."""
    from mercury.storage.host_maintenance import refuse_if_hdd_writes_disabled

    refuse_if_hdd_writes_disabled("destination package preview seal")
    policy = policy or load_retention_policy()
    validation = validate_storage_mount(
        mount_path=mount_root,
        expected_uuid=expected_uuid,
        expected_fstype="ext4",
        require_writable=True,
    )
    if not validation.ok:
        raise RuntimeError(validation.blocker or f"mount validation failed: {validation.code}")

    report = preview_destination_package(
        mount_root,
        run_id=run_id,
        policy=policy,
        mercury_commit=mercury_commit,
        mercury_capture_id=mercury_capture_id,
    )
    if preview_id:
        if "latest" in preview_id.lower():
            raise ValueError("preview ID must not contain unqualified 'latest'")
        report.preview_id = preview_id
    if not report.ok:
        raise RuntimeError(
            "preview not ok; refuse seal: " + "; ".join(report.errors or report.unresolved)
        )
    if report.uses_unqualified_latest:
        raise RuntimeError("preview uses unqualified latest")
    if not report.included:
        raise RuntimeError("preview has no included members")

    payload = preview_to_dict(report)
    fingerprints: list[dict[str, Any]] = []
    for member in report.included:
        if member.path.startswith("(generate:"):
            fingerprints.append({"path": member.path, "logical": True})
            continue
        fingerprints.append(fingerprint_member(Path(member.path), mount_root))
    payload["member_fingerprints"] = fingerprints
    payload["mercury_commit"] = mercury_commit
    payload["mercury_capture_id"] = mercury_capture_id
    payload["sealed_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["preview_sha256"] = compute_preview_sha256(payload)

    out = preview_dir(mount_root, run_id, report.preview_id)
    out.mkdir(parents=True, exist_ok=True)
    preview_path = out / "preview.json"
    _atomic_write_text(preview_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _atomic_write_text(out / "PREVIEW.sha256", payload["preview_sha256"] + "\n")
    return payload


def load_sealed_preview(
    mount_root: Path,
    *,
    run_id: str,
    preview_id: str,
) -> dict[str, Any]:
    if not preview_id or "latest" in preview_id.lower():
        raise ValueError("exact preview ID required (no 'latest')")
    path = preview_dir(mount_root, run_id, preview_id) / "preview.json"
    if not path.is_file():
        raise FileNotFoundError(f"sealed preview missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("preview_id") != preview_id:
        raise ValueError("preview_id mismatch inside sealed preview")
    if payload.get("run_id") != run_id:
        raise ValueError("run_id mismatch inside sealed preview")
    expected = str(payload.get("preview_sha256") or "")
    actual = compute_preview_sha256(payload)
    if not expected or expected != actual:
        raise ValueError(
            f"preview checksum fails: embedded={expected} computed={actual}"
        )
    side = preview_dir(mount_root, run_id, preview_id) / "PREVIEW.sha256"
    if side.is_file():
        side_val = side.read_text(encoding="utf-8").strip()
        if side_val != expected:
            raise ValueError("PREVIEW.sha256 sidecar mismatch")
    return payload


def verify_fingerprints_unchanged(
    mount_root: Path, payload: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    for entry in payload.get("member_fingerprints") or []:
        if entry.get("logical") or entry.get("missing"):
            continue
        path = Path(str(entry["path"]))
        try:
            current = fingerprint_member(path, mount_root)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if current.get("missing"):
            errors.append(f"required source artifact missing: {path}")
            continue
        if entry.get("type") == "file":
            if current.get("sha256") != entry.get("sha256"):
                errors.append(f"source artifact changed since preview: {path}")
        elif entry.get("type") == "dir":
            if current.get("tree_fingerprint") != entry.get("tree_fingerprint"):
                errors.append(f"source artifact tree changed since preview: {path}")
    return errors


def members_from_preview(payload: dict[str, Any]) -> list[PackageMember]:
    out: list[PackageMember] = []
    for raw in payload.get("included") or []:
        out.append(
            PackageMember(
                path=str(raw["path"]),
                kind=str(raw["kind"]),
                identity=str(raw["identity"]),
                mode=str(raw["mode"]),
                size_bytes=int(raw.get("size_bytes") or 0),
                required=bool(raw.get("required", True)),
            )
        )
    return out

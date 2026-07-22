"""Fail-closed destination package creation from an exact sealed preview."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from mercury.core.storage_roles import CONTROL_DIRNAME, DEFAULT_PRIMARY_UUID
from mercury.core.storage_validate import validate_storage_mount
from mercury.migration.destination_documents import (
    DOCUMENT_IDS,
    classify_unresolved_fields,
    evaluate_package_create_preconditions,
    load_destination_documents,
)
from mercury.migration.destination_package import DEFAULT_SCYTALE
from mercury.migration.destination_package_seal import (
    load_sealed_preview,
    members_from_preview,
    verify_fingerprints_unchanged,
)
from mercury.storage.host_maintenance import (
    load_host_maintenance,
    writes_allowed,
)

CREATE_CONFIRMATION = "CREATE DESTINATION PACKAGE"
PACKAGE_SCHEMA = "mercury.destination_package.v1"
EXCLUDED_OBSIDIAN_PREFIX = "obsidiandroid_"


@dataclass
class PackageCreateResult:
    ok: bool
    package_id: str
    package_root: Path | None
    verification_status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    preview_id: str = ""


def packages_root(mount_root: Path) -> Path:
    return mount_root / CONTROL_DIRNAME / "destination_packages"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chmod_restrictive(path: Path) -> None:
    try:
        os.chmod(path, 0o600 if path.is_file() else 0o700)
    except OSError:
        pass


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
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


def _reject_secret_text(text: str) -> list[str]:
    errors: list[str] = []
    for marker in (
        "BEGIN PRIVATE KEY",
        "BEGIN RSA PRIVATE KEY",
        "BEGIN OPENSSH PRIVATE KEY",
    ):
        if marker in text:
            errors.append(f"secret material marker in package: {marker}")
    if re.search(
        r"(?i)(password|passwd|api[_-]?key|token)\s*[:=]\s*['\"]?[^'\"\s]{8,}",
        text,
    ):
        # Allow UNRESOLVED and env *names*
        stripped = re.sub(r"UNRESOLVED_OPERATOR_INPUT", "", text)
        stripped = re.sub(
            r'"name"\s*:\s*"[A-Z0-9_]*(PASSWORD|TOKEN|KEY)[A-Z0-9_]*"',
            '"name":"NAME"',
            stripped,
        )
        if re.search(
            r"(?i)(password|passwd|api[_-]?key|token)\s*[:=]\s*['\"]?[^'\"\s]{8,}",
            stripped,
        ):
            errors.append("possible secret value embedded in package metadata")
    return errors


def _assert_no_excluded_project(path: Path, mount_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        rel_parts = path.resolve().relative_to(mount_root.resolve()).parts
    except ValueError:
        return [f"path escapes mount: {path}"]
    if any(part in DEFAULT_SCYTALE for part in rel_parts):
        errors.append(f"ScytaleDroid path appears in package membership: {path}")
    if any(part.startswith(EXCLUDED_OBSIDIAN_PREFIX) for part in rel_parts):
        if "obsidiandroid" in str(path) and "document" not in str(path).lower():
            # Allow mentioning in documents; refuse as member path under project trees
            if rel_parts and rel_parts[0].startswith(EXCLUDED_OBSIDIAN_PREFIX):
                errors.append(f"excluded ObsidianDroid project path appears: {path}")
    if rel_parts and rel_parts[0] == "mercury_repo_clones":
        errors.append(f"mercury_repo_clones appears: {path}")
    return errors


def _active_ops_blockers() -> list[str]:
    """Best-effort process scan; fail closed on known write ops."""
    errors: list[str] = []
    patterns = (
        "mariadb-dump",
        "mysqldump",
        "rsync",
        "migration package create",
        "storage cleanup execute",
        "storage migrate-run",
    )
    try:
        for pid_dir in Path("/proc").glob("[0-9]*"):
            try:
                cmd = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", errors="replace"
                )
            except OSError:
                continue
            lower = cmd.lower()
            for pat in patterns:
                if pat in lower:
                    errors.append(f"active operation detected: {pat} (pid {pid_dir.name})")
            if "mercury menu" in lower or lower.rstrip().endswith("mercury menu"):
                # Menu alone is not a package-create blocker, but note for detach.
                pass
    except OSError:
        pass
    return errors


def _copy_member(
    source: Path,
    dest_root: Path,
    *,
    mount_root: Path,
    logical_name: str,
) -> tuple[int, int, list[str]]:
    """Copy file/dir into payload; return files, bytes, errors."""
    errors: list[str] = []
    errors.extend(_assert_no_excluded_project(source, mount_root))
    if errors:
        return 0, 0, errors
    if source.is_symlink():
        target = source.resolve()
        if not target.is_relative_to(mount_root.resolve()):
            return 0, 0, [f"symlink escape refused: {source}"]
    dest = dest_root / logical_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = 0
    size = 0
    if source.is_file():
        shutil.copy2(source, dest, follow_symlinks=False)
        files = 1
        size = dest.stat().st_size
        return files, size, errors
    if source.is_dir():
        def _ignore(directory: str, names: list[str]) -> set[str]:
            ignored: set[str] = set()
            for name in names:
                p = Path(directory) / name
                if p.is_symlink():
                    try:
                        if not p.resolve().is_relative_to(mount_root.resolve()):
                            ignored.add(name)
                    except OSError:
                        ignored.add(name)
            return ignored

        shutil.copytree(
            source,
            dest,
            symlinks=False,
            ignore=_ignore,
            dirs_exist_ok=False,
        )
        for dirpath, _dns, filenames in os.walk(dest, followlinks=False):
            for name in filenames:
                fp = Path(dirpath) / name
                if fp.is_file() and not fp.is_symlink():
                    files += 1
                    size += fp.stat().st_size
        return files, size, errors
    return 0, 0, [f"unsupported member type: {source}"]


PHASE3B_BACKUP_IDS = frozenset(
    {
        "erebus_threat_intel_prod-full-20260722_055507_238",
        "android_permission_intel-full-20260722_055648_287",
    }
)


def create_destination_package(
    mount_root: Path,
    *,
    preview_id: str,
    run_id: str = "20260722T055400Z_phase3b",
    confirm: str,
    mercury_commit: str,
    mercury_capture_id: str,
    erebus_commit: str = "3f1bb5bd2229d98b9b76b9f1615238792f12a0b3",
    erebus_capture_id: str = "erebus_destination_candidate_3f1bb5b_20260722T150930Z",
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    package_id: str | None = None,
    expected_backup_ids: frozenset[str] | set[str] | None = None,
    verify_git_head: bool = True,
    repo_root: Path | None = None,
) -> PackageCreateResult:
    """Create a destination package from an exact sealed preview. Never uses 'latest'."""
    errors: list[str] = []
    warnings: list[str] = []

    if confirm != CREATE_CONFIRMATION:
        return PackageCreateResult(
            ok=False,
            package_id="",
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=[f"confirmation must be exactly {CREATE_CONFIRMATION!r}"],
        )
    if not preview_id or "latest" in preview_id.lower():
        return PackageCreateResult(
            ok=False,
            package_id="",
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=["exact preview ID required (no 'latest')"],
        )

    maint = load_host_maintenance()
    if not writes_allowed(maint):
        return PackageCreateResult(
            ok=False,
            package_id="",
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=["host maintenance refuses writes (storage detached or writes_allowed=false)"],
        )

    validation = validate_storage_mount(
        mount_path=mount_root,
        expected_uuid=expected_uuid,
        expected_fstype="ext4",
        require_writable=True,
    )
    if not validation.ok:
        return PackageCreateResult(
            ok=False,
            package_id="",
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=[validation.blocker or f"HDD identity/mount failed: {validation.code}"],
        )

    # Refuse host-shadow: mount must be a real mount of expected UUID.
    if not mount_root.is_mount():
        return PackageCreateResult(
            ok=False,
            package_id="",
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=["destination package target resolves to host-shadow filesystem"],
        )

    errors.extend(_active_ops_blockers())

    try:
        preview = load_sealed_preview(mount_root, run_id=run_id, preview_id=preview_id)
    except (OSError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        return PackageCreateResult(
            ok=False,
            package_id="",
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=[f"preview load/checksum failed: {exc}"],
        )

    if not preview.get("ok"):
        errors.append("preview is not approved/ok")
    if preview.get("uses_unqualified_latest"):
        errors.append("an unqualified latest appears")
    if preview.get("mercury_commit") and preview["mercury_commit"] != mercury_commit:
        errors.append("Mercury commit differs from sealed preview")
    if preview.get("mercury_capture_id") and preview["mercury_capture_id"] != mercury_capture_id:
        errors.append("Mercury capture differs from sealed preview")

    # Live HEAD check (optional for unit fixtures)
    if verify_git_head:
        try:
            import subprocess

            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root or Path(__file__).resolve().parents[3],
                text=True,
            ).strip()
            if head != mercury_commit:
                errors.append(
                    f"Mercury HEAD differs: live={head} expected={mercury_commit}"
                )
        except (OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"unable to verify Mercury HEAD: {exc}")

    for commit_line in preview.get("included_git_commits") or []:
        if commit_line.startswith("current_destination_mercury_commit="):
            pinned = commit_line.split("=", 1)[1]
            if pinned != mercury_commit:
                errors.append("preview git identity mercury commit mismatch")

    backup_ids = list(preview.get("included_backup_ids") or [])
    expected_backups = set(expected_backup_ids or PHASE3B_BACKUP_IDS)
    if set(backup_ids) != expected_backups:
        errors.append(f"Phase 3B backup IDs differ: {backup_ids}")

    if erebus_capture_id not in (preview.get("included_capture_ids") or []):
        errors.append("Erebus capture differs / missing from preview")
    if mercury_capture_id not in (preview.get("included_capture_ids") or []):
        errors.append("Mercury capture missing from preview")

    errors.extend(verify_fingerprints_unchanged(mount_root, preview))

    docs = load_destination_documents(mount_root, run_id)
    for row in classify_unresolved_fields(docs):
        if (
            row["class"] == "PACKAGE_CREATION_BLOCKER"
            or row["required_before_package_create"] == "yes"
        ):
            errors.append(
                f"package-creation-blocking unresolved field: {row['document']}:{row['field']}"
            )

    preflight = evaluate_package_create_preconditions(
        preview_id=preview_id,
        preview_checksum=str(preview.get("preview_sha256") or ""),
        expected_preview_checksum=str(preview.get("preview_sha256") or ""),
        source_artifacts_unchanged=not any("changed since preview" in e for e in errors),
        members_match_preview=True,
        uses_unqualified_latest=bool(preview.get("uses_unqualified_latest")),
        protected_checksum_ok=not any("checksum" in e.lower() for e in errors),
        scytale_or_obsidian_present=any("Scytale" in e or "Obsidian" in e for e in errors),
        active_hdd_identity_ok=validation.ok,
        documents=docs,
    )
    errors.extend([e for e in preflight if e not in errors])

    members = members_from_preview(preview)
    if not members:
        errors.append("sealed preview has no included members")

    for member in members:
        if member.path.startswith("(generate:"):
            errors.append(f"unresolved logical member remains: {member.path}")
            continue
        errors.extend(_assert_no_excluded_project(Path(member.path), mount_root))
        if "latest" in member.identity.lower() or "latest" in member.path.lower():
            errors.append(f"unqualified latest in member: {member.identity}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pkg_id = package_id or f"destination_rehearsal_{run_id}_{stamp}"
    if "latest" in pkg_id.lower():
        errors.append("package ID must not contain latest")

    final_root = packages_root(mount_root) / pkg_id
    if final_root.exists():
        return PackageCreateResult(
            ok=False,
            package_id=pkg_id,
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=[f"package ID already exists: {final_root}"],
        )

    if errors:
        return PackageCreateResult(
            ok=False,
            package_id=pkg_id,
            package_root=None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=errors,
            warnings=warnings,
            preview_id=preview_id,
        )

    packages_root(mount_root).mkdir(parents=True, exist_ok=True)
    tmp_root = packages_root(mount_root) / f".{pkg_id}.partial"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(parents=True)
    payload_dir = tmp_root / "payload"
    docs_dir = tmp_root / "destination_documents"
    payload_dir.mkdir()
    docs_dir.mkdir()

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inventory: list[dict[str, Any]] = []
    member_records: list[dict[str, Any]] = []
    total_files = 0
    total_bytes = 0

    try:
        for idx, member in enumerate(members):
            source = Path(member.path)
            logical = f"{idx:03d}_{member.kind}_{re.sub(r'[^A-Za-z0-9._-]+', '_', member.identity)[:80]}"
            files, size, copy_errors = _copy_member(
                source, payload_dir, mount_root=mount_root, logical_name=logical
            )
            if copy_errors:
                raise RuntimeError("; ".join(copy_errors))
            total_files += files
            total_bytes += size
            dest_path = payload_dir / logical
            member_records.append(
                {
                    "preview_path": member.path,
                    "kind": member.kind,
                    "identity": member.identity,
                    "preview_mode": member.mode,
                    "package_relative": f"payload/{logical}",
                    "copied": True,
                    "file_count": files,
                    "size_bytes": size,
                }
            )
            # Inventory files
            if dest_path.is_file():
                inventory.append(
                    {
                        "relative_path": f"payload/{logical}",
                        "sha256": _sha256_file(dest_path),
                        "size_bytes": dest_path.stat().st_size,
                    }
                )
            else:
                for dirpath, _dns, filenames in os.walk(dest_path, followlinks=False):
                    for name in filenames:
                        fp = Path(dirpath) / name
                        if not fp.is_file() or fp.is_symlink():
                            continue
                        rel = fp.relative_to(tmp_root).as_posix()
                        inventory.append(
                            {
                                "relative_path": rel,
                                "sha256": _sha256_file(fp),
                                "size_bytes": fp.stat().st_size,
                            }
                        )

        # Copy destination documents into package control section
        for doc_id, doc in docs.items():
            dest = docs_dir / f"{doc_id}.json"
            shutil.copy2(doc.path, dest)
            _chmod_restrictive(dest)
            inventory.append(
                {
                    "relative_path": f"destination_documents/{doc_id}.json",
                    "sha256": _sha256_file(dest),
                    "size_bytes": dest.stat().st_size,
                }
            )

        # Sealed preview copy
        src_preview = (
            mount_root
            / CONTROL_DIRNAME
            / "destination"
            / run_id
            / "previews"
            / preview_id
            / "preview.json"
        )
        shutil.copy2(src_preview, tmp_root / "source_preview.json")
        _chmod_restrictive(tmp_root / "source_preview.json")
        inventory.append(
            {
                "relative_path": "source_preview.json",
                "sha256": _sha256_file(tmp_root / "source_preview.json"),
                "size_bytes": (tmp_root / "source_preview.json").stat().st_size,
            }
        )

        unresolved_rows = classify_unresolved_fields(docs)
        finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        manifest = {
            "schema": PACKAGE_SCHEMA,
            "package_id": pkg_id,
            "preview_id": preview_id,
            "run_id": run_id,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "mercury_commit": mercury_commit,
            "mercury_capture_id": mercury_capture_id,
            "erebus_commit": erebus_commit,
            "erebus_capture_id": erebus_capture_id,
            "included_backup_ids": backup_ids,
            "mount_uuid": expected_uuid,
            "member_count": len(member_records),
            "file_count": len(inventory),
            "total_bytes": sum(i["size_bytes"] for i in inventory),
            "members": member_records,
            "unresolved_operator_inputs": unresolved_rows,
        }
        _atomic_write_json(tmp_root / "package_manifest.json", manifest)
        _atomic_write_json(
            tmp_root / "package_file_inventory.json",
            {"package_id": pkg_id, "files": inventory},
        )
        sums_lines = [
            f"{item['sha256']}  {item['relative_path']}" for item in sorted(
                inventory, key=lambda x: x["relative_path"]
            )
        ]
        (tmp_root / "package_members.sha256").write_text(
            "\n".join(sums_lines) + "\n", encoding="utf-8"
        )
        _chmod_restrictive(tmp_root / "package_members.sha256")

        # Verify membership vs preview
        verify_errors: list[str] = []
        preview_idents = {(m.kind, m.identity) for m in members}
        pkg_idents = {(m["kind"], m["identity"]) for m in member_records}
        missing = preview_idents - pkg_idents
        extra = pkg_idents - preview_idents
        if missing:
            verify_errors.append(f"missing members: {sorted(missing)}")
        if extra:
            verify_errors.append(f"unexpected members: {sorted(extra)}")

        expected_payload = {m["package_relative"].split("/", 1)[1] for m in member_records}
        actual_payload = {p.name for p in payload_dir.iterdir()} if payload_dir.is_dir() else set()
        unexpected_payload = actual_payload - expected_payload
        if unexpected_payload:
            verify_errors.append(f"unexpected members: {sorted(unexpected_payload)}")
        missing_payload = expected_payload - actual_payload
        if missing_payload:
            verify_errors.append(f"missing members: {sorted(missing_payload)}")

        # SHA verify
        for item in inventory:
            path = tmp_root / item["relative_path"]
            if not path.is_file():
                verify_errors.append(f"inventory file missing: {item['relative_path']}")
                continue
            actual = _sha256_file(path)
            if actual != item["sha256"]:
                verify_errors.append(f"checksum mismatch: {item['relative_path']}")

        # Re-check secrets in control JSON
        for name in (
            "package_manifest.json",
            "package_file_inventory.json",
            "source_preview.json",
        ):
            verify_errors.extend(
                _reject_secret_text((tmp_root / name).read_text(encoding="utf-8", errors="replace"))
            )
        for doc_id in DOCUMENT_IDS:
            p = docs_dir / f"{doc_id}.json"
            if p.is_file():
                verify_errors.extend(
                    _reject_secret_text(p.read_text(encoding="utf-8", errors="replace"))
                )

        # No absolute paths in inventory relative_path
        for item in inventory:
            if item["relative_path"].startswith("/"):
                verify_errors.append(f"absolute path in inventory: {item['relative_path']}")

        status = (
            "DESTINATION_PACKAGE_VERIFIED"
            if not verify_errors
            else "DESTINATION_PACKAGE_FAILED"
        )
        receipt = {
            "package_id": pkg_id,
            "preview_id": preview_id,
            "verification_status": status,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "mercury_commit": mercury_commit,
            "mercury_capture_id": mercury_capture_id,
            "erebus_commit": erebus_commit,
            "erebus_capture_id": erebus_capture_id,
            "backup_ids": backup_ids,
            "file_count": len(inventory),
            "total_bytes": sum(i["size_bytes"] for i in inventory),
            "errors": verify_errors,
            "unresolved_operator_inputs": unresolved_rows,
        }
        _atomic_write_json(tmp_root / "package_receipt.json", receipt)
        _atomic_write_json(
            tmp_root / "verification_report.json",
            {
                "status": status,
                "errors": verify_errors,
                "checked_at_utc": finished,
                "preview_member_count": len(members),
                "package_member_count": len(member_records),
                "inventory_file_count": len(inventory),
            },
        )

        if verify_errors:
            # Leave partial for debugging but do not promote
            return PackageCreateResult(
                ok=False,
                package_id=pkg_id,
                package_root=tmp_root,
                verification_status=status,
                errors=verify_errors,
                preview_id=preview_id,
                file_count=len(inventory),
                total_bytes=sum(i["size_bytes"] for i in inventory),
            )

        _fsync_dir(payload_dir)
        _fsync_dir(tmp_root)
        os.replace(tmp_root, final_root)
        _fsync_dir(final_root.parent)

        return PackageCreateResult(
            ok=True,
            package_id=pkg_id,
            package_root=final_root,
            verification_status="DESTINATION_PACKAGE_VERIFIED",
            warnings=warnings,
            preview_id=preview_id,
            file_count=len(inventory),
            total_bytes=sum(i["size_bytes"] for i in inventory),
        )
    except Exception as exc:  # noqa: BLE001 — fail closed, keep partial hidden
        errors.append(str(exc))
        return PackageCreateResult(
            ok=False,
            package_id=pkg_id,
            package_root=tmp_root if tmp_root.exists() else None,
            verification_status="DESTINATION_PACKAGE_FAILED",
            errors=errors,
            preview_id=preview_id,
        )

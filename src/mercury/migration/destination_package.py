"""Destination package allowlist preview (no package creation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from mercury.backup.find_latest_backup import find_backup_by_id
from mercury.core.storage_roles import CONTROL_DIRNAME
from mercury.storage.retention import RetentionPolicy, load_retention_policy

UNRESOLVED_MERCURY_CAPTURE = "pending final Mercury main commit and capture"
DEFAULT_SCYTALE = frozenset(
    {
        "scytaledroid_migration_checkpoints",
        "scytaledroid_apk_store_backups",
        "scytaledroid_artifacts",
    }
)


@dataclass(frozen=True)
class PackageMember:
    path: str
    kind: str
    identity: str
    mode: str  # reference | copy
    size_bytes: int
    required: bool = True


@dataclass
class DestinationPackagePreview:
    run_id: str
    generated_at: str
    mount_root: str
    included: list[PackageMember] = field(default_factory=list)
    excluded_top_level: list[str] = field(default_factory=list)
    intake_included: list[str] = field(default_factory=list)
    intake_excluded: list[str] = field(default_factory=list)
    included_backup_ids: list[str] = field(default_factory=list)
    included_git_commits: list[str] = field(default_factory=list)
    included_capture_ids: list[str] = field(default_factory=list)
    estimated_size_bytes: int = 0
    file_count: int = 0
    manifest_reference_count: int = 0
    unresolved: list[str] = field(default_factory=list)
    uses_unqualified_latest: bool = False
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    preview_id: str = ""


# Erebus intake allowlist / denylist (do not copy whole tree).
INTAKE_INCLUDE_RELATIVE: tuple[str, ...] = (
    "intake_contract.json",
    "README.md",
    "manifests",
    "ingest_ready",
    "prepared",
    "notes",
)
INTAKE_EXCLUDE_RELATIVE: tuple[str, ...] = (
    "downloads",
    "archive",
    "logs",
    "tools",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_files_and_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return 1, path.lstat().st_size
        except OSError:
            return 0, 0
    files = 0
    size = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            files += 1
            try:
                size += (Path(dirpath) / name).lstat().st_size
            except OSError:
                continue
    return files, size


def _assert_under_root(path: Path, mount_root: Path) -> None:
    resolved = path.resolve()
    root = mount_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"path escapes approved Mercury root: {path}")


def _reject_symlink_escape(path: Path, mount_root: Path) -> None:
    if path.is_symlink():
        target = path.resolve()
        if not target.is_relative_to(mount_root.resolve()):
            raise ValueError(f"symlink escape refused: {path} -> {target}")


def preview_destination_package(
    mount_root: Path,
    *,
    run_id: str,
    policy: RetentionPolicy | None = None,
    allow_scytaledroid: bool = False,
    scytaledroid_paths: list[str] | None = None,
    mercury_commit: str | None = None,
    mercury_capture_id: str | None = None,
) -> DestinationPackagePreview:
    """Build an allowlist preview. Never creates a package."""
    policy = policy or load_retention_policy()
    mercury_commit = (mercury_commit or policy.current_destination_mercury_commit or "").strip()
    mercury_capture_id = (
        mercury_capture_id or policy.current_destination_mercury_capture_id or ""
    ).strip()
    report = DestinationPackagePreview(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mount_root=str(mount_root),
        preview_id=f"preview_{run_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        intake_included=list(INTAKE_INCLUDE_RELATIVE),
        intake_excluded=list(INTAKE_EXCLUDE_RELATIVE),
    )

    if "latest" in run_id.lower():
        report.uses_unqualified_latest = True
        report.errors.append("run-id must not use unqualified 'latest'")

    if run_id not in policy.protected_run_ids and run_id != "20260722T055400Z_phase3b":
        # Still allow preview for the protected Phase 3B id only by default.
        if not policy.protects_run_id(run_id):
            report.errors.append(
                f"run-id '{run_id}' is not in protected_run_ids; refuse unknown package root"
            )

    # Default exclusions
    report.excluded_top_level = sorted(set(policy.exclude_from_destination_by_default))
    for name in policy.manual_review_roots:
        if name not in report.excluded_top_level:
            report.excluded_top_level.append(name)
    report.excluded_top_level = sorted(set(report.excluded_top_level))

    # Scytale gate
    if allow_scytaledroid or policy.allow_scytaledroid_in_destination:
        approved = list(scytaledroid_paths or policy.scytaledroid_approved_paths)
        if not approved:
            report.errors.append(
                "ScytaleDroid inclusion requires allow flag AND exact path list"
            )
        for rel in approved:
            path = mount_root / rel if not Path(rel).is_absolute() else Path(rel)
            try:
                _assert_under_root(path, mount_root)
                _reject_symlink_escape(path, mount_root)
            except ValueError as exc:
                report.errors.append(str(exc))
            if path.name in policy.manual_review_roots or any(
                part in policy.manual_review_roots for part in path.parts
            ):
                # Explicit approval path — still not auto-included without listing.
                files, size = _count_files_and_size(path)
                report.included.append(
                    PackageMember(
                        path=str(path),
                        kind="scytaledroid_approved",
                        identity=rel,
                        mode="copy",
                        size_bytes=size,
                        required=False,
                    )
                )
                report.file_count += files
                report.estimated_size_bytes += size
    else:
        # Fail if any Scytale path somehow appears in members later.
        pass

    # Phase 3B evidence
    phase_root = mount_root / CONTROL_DIRNAME / "phase3b" / run_id
    if not phase_root.is_dir():
        report.errors.append(f"required Phase 3B run missing: {run_id}")
        report.unresolved.append(f"phase3b:{run_id}")
    else:
        try:
            _assert_under_root(phase_root, mount_root)
        except ValueError as exc:
            report.errors.append(str(exc))
        files, size = _count_files_and_size(phase_root)
        report.included.append(
            PackageMember(
                path=str(phase_root),
                kind="phase3b_run",
                identity=run_id,
                mode="copy",
                size_bytes=size,
            )
        )
        report.file_count += files
        report.estimated_size_bytes += size
        report.manifest_reference_count += 1

    # Protected production backup IDs (reference governed copies under mercury_backups)
    backup_root = mount_root / "mercury_backups"
    for backup_id in policy.protected_backup_ids:
        if "latest" in backup_id.lower():
            report.uses_unqualified_latest = True
            report.errors.append(f"backup id uses unqualified latest: {backup_id}")
            continue
        path = find_backup_by_id(backup_root, backup_id)
        if path is None:
            report.errors.append(f"missing exact backup id: {backup_id}")
            report.unresolved.append(backup_id)
            continue
        try:
            _assert_under_root(path, mount_root)
            _reject_symlink_escape(path, mount_root)
        except ValueError as exc:
            report.errors.append(str(exc))
            continue
        # Verify declared dump checksum when sidecar lists it (fail closed on mismatch).
        checksum_path = path / "checksum.sha256"
        manifest_path = path / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                expected = str(manifest.get("sha256") or "").strip()
                dump_name = str(manifest.get("dump_file") or "").strip()
                if expected and dump_name:
                    dump = path / dump_name
                    if dump.is_file():
                        actual = _sha256_file(dump)
                        if actual != expected:
                            report.errors.append(
                                f"checksum mismatch for {backup_id}: manifest vs dump"
                            )
            except (OSError, json.JSONDecodeError) as exc:
                report.errors.append(f"manifest unreadable for {backup_id}: {exc}")
        files, size = _count_files_and_size(path)
        report.included.append(
            PackageMember(
                path=str(path),
                kind="backup",
                identity=backup_id,
                mode="reference",
                size_bytes=size,
            )
        )
        report.included_backup_ids.append(backup_id)
        report.file_count += files
        report.estimated_size_bytes += size
        if checksum_path.is_file():
            report.manifest_reference_count += 1

    # Erebus capture
    for capture_id in policy.protected_capture_ids:
        capture_path = (
            mount_root / CONTROL_DIRNAME / "validation" / "erebus" / capture_id
        )
        if not capture_path.exists():
            report.errors.append(f"required capture missing: {capture_id}")
            report.unresolved.append(capture_id)
            continue
        files, size = _count_files_and_size(capture_path)
        report.included.append(
            PackageMember(
                path=str(capture_path),
                kind="erebus_capture",
                identity=capture_id,
                mode="copy",
                size_bytes=size,
            )
        )
        report.included_capture_ids.append(capture_id)
        report.file_count += files
        report.estimated_size_bytes += size

    # Historical Phase 3B Mercury commit (identity only — not destination candidate)
    if policy.historical_phase3b_mercury_commit:
        report.included_git_commits.append(
            f"historical_phase3b_mercury_commit={policy.historical_phase3b_mercury_commit}"
        )

    if not mercury_commit or not mercury_capture_id:
        report.unresolved.append(UNRESOLVED_MERCURY_CAPTURE)
    else:
        if "latest" in mercury_commit.lower() or "latest" in mercury_capture_id.lower():
            report.uses_unqualified_latest = True
            report.errors.append("Mercury commit/capture must not use unqualified latest")
        report.included_git_commits.append(
            f"current_destination_mercury_commit={mercury_commit}"
        )
        report.included_capture_ids.append(mercury_capture_id)
        capture_path = (
            mount_root / CONTROL_DIRNAME / "validation" / "mercury" / mercury_capture_id
        )
        if not capture_path.is_dir():
            report.errors.append(f"required Mercury capture missing: {mercury_capture_id}")
            report.unresolved.append(mercury_capture_id)
        else:
            identity_file = capture_path / "capture_identity.json"
            if identity_file.is_file():
                try:
                    identity = json.loads(identity_file.read_text(encoding="utf-8"))
                    pinned = str(identity.get("commit") or "").strip()
                    if pinned and pinned != mercury_commit:
                        report.errors.append(
                            "Mercury capture identity commit mismatch: "
                            f"arg={mercury_commit} file={pinned}"
                        )
                except (OSError, json.JSONDecodeError) as exc:
                    report.errors.append(f"Mercury capture identity unreadable: {exc}")
            files, size = _count_files_and_size(capture_path)
            report.included.append(
                PackageMember(
                    path=str(capture_path),
                    kind="mercury_capture",
                    identity=mercury_capture_id,
                    mode="copy",
                    size_bytes=size,
                )
            )
            report.file_count += files
            report.estimated_size_bytes += size

    # Erebus intake — allowlisted subset only (never whole tree).
    intake = mount_root / "erebus-intake"
    if intake.is_dir():
        contract = intake / "intake_contract.json"
        if not contract.is_file():
            report.errors.append("erebus-intake missing intake_contract.json")
            report.unresolved.append("erebus-intake/intake_contract.json")
        else:
            contract_sha = _sha256_file(contract)
            files, size = _count_files_and_size(contract)
            report.included.append(
                PackageMember(
                    path=str(contract),
                    kind="erebus_intake_contract",
                    identity=f"sha256:{contract_sha}",
                    mode="copy",
                    size_bytes=size,
                )
            )
            report.file_count += files
            report.estimated_size_bytes += size
            report.manifest_reference_count += 1
        for rel in INTAKE_INCLUDE_RELATIVE:
            if rel == "intake_contract.json":
                continue
            path = intake / rel
            if not path.exists():
                report.unresolved.append(f"erebus-intake/{rel}")
                continue
            try:
                _assert_under_root(path, mount_root)
                _reject_symlink_escape(path, mount_root)
            except ValueError as exc:
                report.errors.append(str(exc))
                continue
            files, size = _count_files_and_size(path)
            digest = _sha256_file(path) if path.is_file() else f"tree:{rel}"
            report.included.append(
                PackageMember(
                    path=str(path),
                    kind="erebus_intake",
                    identity=f"{rel}:{digest if path.is_file() else 'dir'}",
                    mode="copy",
                    size_bytes=size,
                )
            )
            report.file_count += files
            report.estimated_size_bytes += size
        for rel in INTAKE_EXCLUDE_RELATIVE:
            report.excluded_top_level.append(f"erebus-intake/{rel}")
    else:
        report.unresolved.append("erebus-intake")

    # Required runbooks / manifests (latest complete transfer only if pinned path exists)
    for rel in ("mercury_runbooks", "mercury_manifests", "mercury_state"):
        path = mount_root / rel
        if not path.exists():
            report.unresolved.append(rel)
            continue
        files, size = _count_files_and_size(path)
        report.included.append(
            PackageMember(
                path=str(path),
                kind=rel,
                identity=rel,
                mode="copy",
                size_bytes=size,
            )
        )
        report.file_count += files
        report.estimated_size_bytes += size
        report.manifest_reference_count += files

    # Phase 3B worktree captures
    for stamp in ("20260722T055310Z", "20260722T055352Z"):
        path = mount_root / "mercury_worktree_snapshots" / stamp
        if path.is_dir():
            files, size = _count_files_and_size(path)
            report.included.append(
                PackageMember(
                    path=str(path),
                    kind="worktree_capture",
                    identity=stamp,
                    mode="copy",
                    size_bytes=size,
                )
            )
            report.included_capture_ids.append(stamp)
            report.file_count += files
            report.estimated_size_bytes += size
        else:
            report.unresolved.append(f"worktree:{stamp}")

    # Ensure no Scytale path snuck in without approval
    for member in report.included:
        parts = Path(member.path).parts
        if any(part in DEFAULT_SCYTALE for part in parts):
            if not (allow_scytaledroid or policy.allow_scytaledroid_in_destination):
                report.errors.append(
                    f"ScytaleDroid data appears without explicit approval: {member.path}"
                )

    # Destination package docs (governed evidence under .mercury_control/destination/)
    from mercury.migration.destination_documents import (
        DOCUMENT_IDS,
        load_destination_documents,
        resolve_active_documents_dir,
        validate_documents_against_preview_pins,
    )

    loaded_docs = load_destination_documents(mount_root, run_id)
    doc_mercury_commit = (mercury_commit or policy.current_destination_mercury_commit or "").strip()
    doc_mercury_capture = (
        mercury_capture_id or policy.current_destination_mercury_capture_id or ""
    ).strip()
    doc_erebus_commit = (policy.current_erebus_destination_commit or "").strip()
    if not doc_erebus_commit and loaded_docs:
        sample = next(iter(loaded_docs.values()))
        doc_erebus_commit = str(sample.payload.get("erebus_commit") or "").strip()
    if loaded_docs:
        pin_errors = validate_documents_against_preview_pins(
            loaded_docs,
            run_id=run_id,
            mercury_commit=doc_mercury_commit,
            mercury_capture_id=doc_mercury_capture,
            erebus_commit=doc_erebus_commit,
            protected_backup_ids=policy.protected_backup_ids,
        )
        if pin_errors:
            report.errors.extend(pin_errors)
    for doc_id in DOCUMENT_IDS:
        doc = loaded_docs.get(doc_id)
        if doc is None:
            report.included.append(
                PackageMember(
                    path=f"(generate:{doc_id})",
                    kind="document",
                    identity=doc_id,
                    mode="copy",
                    size_bytes=0,
                    required=True,
                )
            )
            report.unresolved.append(f"document:{doc_id}")
            continue
        try:
            _assert_under_root(doc.path, mount_root)
            _reject_symlink_escape(doc.path, mount_root)
        except ValueError as exc:
            report.errors.append(str(exc))
            report.unresolved.append(f"document:{doc_id}")
            continue
        size = doc.path.stat().st_size if doc.path.is_file() else 0
        report.included.append(
            PackageMember(
                path=str(doc.path),
                kind="document",
                identity=doc_id,
                mode="copy",
                size_bytes=size,
                required=True,
            )
        )
        report.file_count += 1
        report.estimated_size_bytes += size
        report.manifest_reference_count += 1

    docs_root = resolve_active_documents_dir(mount_root, run_id)
    if (
        docs_root is not None
        and loaded_docs
        and len(loaded_docs) == len(DOCUMENT_IDS)
        and not any(u.startswith("document:") for u in report.unresolved)
    ):
        for support_name in ("documents_index.json", "SHA256SUMS"):
            support = docs_root / support_name
            if not support.is_file():
                continue
            try:
                _assert_under_root(support, mount_root)
                _reject_symlink_escape(support, mount_root)
            except ValueError as exc:
                report.errors.append(str(exc))
                continue
            size = support.stat().st_size
            report.included.append(
                PackageMember(
                    path=str(support),
                    kind="document_support",
                    identity=support_name,
                    mode="copy",
                    size_bytes=size,
                    required=True,
                )
            )
            report.file_count += 1
            report.estimated_size_bytes += size
            report.manifest_reference_count += 1

    hard_unresolved = [
        item
        for item in report.unresolved
        if item.startswith("phase3b:")
        or item in set(policy.protected_backup_ids)
        or item in set(policy.protected_capture_ids)
        or item.startswith("worktree:")
    ]
    report.ok = (
        not report.errors
        and not hard_unresolved
        and not report.uses_unqualified_latest
    )
    if UNRESOLVED_MERCURY_CAPTURE in report.unresolved:
        report.ok = False
        if UNRESOLVED_MERCURY_CAPTURE not in report.errors:
            report.errors.append(UNRESOLVED_MERCURY_CAPTURE)
    return report

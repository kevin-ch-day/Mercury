"""Generate and resolve destination-package planning documents (no package create)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import socket
import tempfile
from pathlib import Path
from typing import Any

from mercury.core.storage_roles import CONTROL_DIRNAME, DEFAULT_PRIMARY_UUID
from mercury.core.storage_validate import validate_storage_mount
from mercury.storage.retention import RetentionPolicy, load_retention_policy

DOCUMENT_SCHEMA = "mercury.destination_document.v1"
DOCUMENT_SCHEMA_VERSION = "1.0.0"
LINKED_PREVIEW_ID = "preview_20260722T055400Z_phase3b_20260722T180645Z"
UNRESOLVED = "UNRESOLVED_OPERATOR_INPUT"

DOCUMENT_IDS: tuple[str, ...] = (
    "source_host_inventory",
    "environment_secret_name_inventory",
    "destination_acceptance_checklist",
    "rollback_instructions",
)

DOCUMENT_FILENAMES: dict[str, str] = {
    doc_id: f"{doc_id}.json" for doc_id in DOCUMENT_IDS
}

EXCLUDED_PACKAGE_TREES: tuple[str, ...] = (
    "scytaledroid_migration_checkpoints",
    "scytaledroid_apk_store_backups",
    "scytaledroid_artifacts",
    "mercury_repo_clones",
    "obsidiandroid_core_accounts",
    "obsidiandroid_core_phase2b_preprovisioning",
    "obsidiandroid_core_provisioning",
    "obsidiandroid_core_results_provisioning",
    "obsidiandroid_cutover_readiness",
    "obsidiandroid_phase2c",
    "obsidiandroid_phase2c_rehearsal",
    "obsidiandroid_release_artifacts",
)


@dataclass
class DestinationDocument:
    document_id: str
    path: Path
    sha256: str
    unresolved_field_count: int
    payload: dict[str, Any]


@dataclass
class DocumentGenerationResult:
    run_id: str
    documents_dir: Path
    linked_preview_id: str
    documents: list[DestinationDocument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mount_uuid_ok: bool = False

    @property
    def ok(self) -> bool:
        return (
            self.mount_uuid_ok
            and not self.errors
            and len(self.documents) == len(DOCUMENT_IDS)
            and all(d.document_id in DOCUMENT_IDS for d in self.documents)
        )


def destination_run_root(mount_root: Path, run_id: str) -> Path:
    return mount_root / CONTROL_DIRNAME / "destination" / run_id


def legacy_documents_dir(mount_root: Path, run_id: str) -> Path:
    """Historical first-generation documents directory (never overwritten by new runs)."""
    return destination_run_root(mount_root, run_id) / "documents"


def documents_runs_root(mount_root: Path, run_id: str) -> Path:
    return destination_run_root(mount_root, run_id) / "documents_runs"


def documents_dir_for(
    mount_root: Path,
    run_id: str,
    *,
    documents_run: str | None = None,
) -> Path:
    """Return a concrete documents directory.

    New generations write under ``documents_runs/<documents_run>/``.
    The original ``documents/`` tree is preserved as historical evidence.
    """
    if documents_run:
        return documents_runs_root(mount_root, run_id) / documents_run
    return legacy_documents_dir(mount_root, run_id)


def document_path(
    mount_root: Path,
    run_id: str,
    document_id: str,
    *,
    documents_run: str | None = None,
) -> Path:
    return documents_dir_for(
        mount_root, run_id, documents_run=documents_run
    ) / DOCUMENT_FILENAMES[document_id]


def _documents_dir_complete(path: Path) -> bool:
    return all((path / DOCUMENT_FILENAMES[doc_id]).is_file() for doc_id in DOCUMENT_IDS)


def resolve_active_documents_dir(
    mount_root: Path,
    run_id: str,
    *,
    documents_run: str | None = None,
) -> Path | None:
    """Prefer an explicit/latest ``documents_runs`` generation; else legacy ``documents/``."""
    if documents_run:
        candidate = documents_dir_for(mount_root, run_id, documents_run=documents_run)
        return candidate if _documents_dir_complete(candidate) else None
    runs_root = documents_runs_root(mount_root, run_id)
    if runs_root.is_dir():
        runs = sorted(
            (p for p in runs_root.iterdir() if p.is_dir() and _documents_dir_complete(p)),
            key=lambda p: p.name,
        )
        if runs:
            return runs[-1]
    legacy = legacy_documents_dir(mount_root, run_id)
    if _documents_dir_complete(legacy):
        return legacy
    return None


# Classification for UNRESOLVED_OPERATOR_INPUT fields.
UnresolvedClass = str  # PACKAGE_CREATION_BLOCKER | DESTINATION_PREP_REQUIRED | ...

UNRESOLVED_FIELD_POLICY: dict[str, tuple[UnresolvedClass, str, str]] = {
    # path_suffix -> (class, required_before_package_create yes/no, destination step)
    "destination_system_requirements.filesystem_and_mounts.destination_mount_path": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "8. Configure Mercury storage contract",
    ),
    "destination_system_requirements.mariadb_compatibility.destination_required_minimum": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "4. Install destination prerequisites",
    ),
    "destination_system_requirements.ports_and_firewall.additional_ports": (
        "OPTIONAL_DOCUMENTATION",
        "no",
        "4. Install destination prerequisites",
    ),
    "destination_system_requirements.ports_and_firewall.firewall_policy": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "4. Install destination prerequisites",
    ),
    "destination_system_requirements.required_directories_and_permissions.erebus_checkout": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "6. Reconstruct Erebus",
    ),
    "destination_system_requirements.required_directories_and_permissions.mercury_checkout": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "5. Reconstruct Mercury",
    ),
    "destination_system_requirements.required_directories_and_permissions.operator_storage_mount": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "8. Configure Mercury storage contract",
    ),
    "destination_system_requirements.required_free_disk_space.destination_free_space_required_gib": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "4. Install destination prerequisites",
    ),
    "destination_system_requirements.required_packages[4].value": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "4. Install destination prerequisites",
    ),
    "destination_system_requirements.required_system_users_and_groups.mariadb_unix_socket_peer_user": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "7. Provision configuration and secrets",
    ),
    "destination_system_requirements.required_system_users_and_groups.mercury_operator_account": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "4. Install destination prerequisites",
    ),
    "erebus.values_entered_manually[4]": (
        "RESTORE_EXECUTION_REQUIRED",
        "no",
        "7. Provision configuration and secrets",
    ),
    "file_paths_for_secret_provisioning[0].status": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "7. Provision configuration and secrets",
    ),
    "file_paths_for_secret_provisioning[1].status": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "7. Provision configuration and secrets",
    ),
    "mercury.values_entered_manually[2]": (
        "RESTORE_EXECUTION_REQUIRED",
        "no",
        "7. Provision configuration and secrets",
    ),
    "intake_reconstruction.destination_path": (
        "RESTORE_EXECUTION_REQUIRED",
        "no",
        "11. Validate Erebus intake subset",
    ),
    "intake_reconstruction.ownership_and_permissions": (
        "RESTORE_EXECUTION_REQUIRED",
        "no",
        "11. Validate Erebus intake subset",
    ),
    "repository_reconstruction.erebus.dependency_install_order": (
        "DESTINATION_PREP_REQUIRED",
        "no",
        "6. Reconstruct Erebus",
    ),
}


def classify_unresolved_fields(
    documents: dict[str, DestinationDocument],
) -> list[dict[str, str]]:
    """Return classified UNRESOLVED_OPERATOR_INPUT rows for operator reporting."""
    rows: list[dict[str, str]] = []

    def walk(obj: Any, prefix: str, document_id: str) -> None:
        if obj == UNRESOLVED:
            policy = UNRESOLVED_FIELD_POLICY.get(prefix)
            if policy is None:
                cls, pkg, step = (
                    "VALIDATION_REQUIRED",
                    "no",
                    "13. Record destination acceptance evidence",
                )
            else:
                cls, pkg, step = policy
            rows.append(
                {
                    "field": prefix,
                    "document": document_id,
                    "class": cls,
                    "required_before_package_create": pkg,
                    "destination_step": step,
                    "who_supplies": "destination_operator",
                }
            )
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                walk(value, f"{prefix}.{key}" if prefix else key, document_id)
        elif isinstance(obj, list):
            for idx, value in enumerate(obj):
                walk(value, f"{prefix}[{idx}]", document_id)

    for doc_id, doc in documents.items():
        walk(doc.payload.get("body"), "", doc_id)
    return rows


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _count_unresolved(obj: Any) -> int:
    if obj == UNRESOLVED:
        return 1
    if isinstance(obj, dict):
        return sum(_count_unresolved(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_count_unresolved(v) for v in obj)
    return 0


def _assert_no_secret_values(payload: dict[str, Any]) -> list[str]:
    """Fail closed if payload appears to embed credential material."""
    errors: list[str] = []
    text = json.dumps(payload, sort_keys=True)
    forbidden = (
        "BEGIN PRIVATE KEY",
        "BEGIN RSA PRIVATE KEY",
        "BEGIN OPENSSH PRIVATE KEY",
        "AKIA",  # AWS-like
    )
    for token in forbidden:
        if token in text:
            errors.append(f"forbidden secret material marker present: {token}")
    # Reject obvious password assignments in string values.
    password_like = re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s]{8,}"
    )
    if password_like.search(text):
        # Allow the literal UNRESOLVED and env *names* ending in PASSWORD.
        stripped = re.sub(r'"[^"]*PASSWORD[^"]*"\s*:\s*"UNRESOLVED_OPERATOR_INPUT"', "", text)
        stripped = re.sub(
            r'"name"\s*:\s*"[A-Z0-9_]*PASSWORD[A-Z0-9_]*"',
            '"name":"REDACTED_NAME"',
            stripped,
        )
        stripped = re.sub(
            r'"variable"\s*:\s*"[A-Z0-9_]*PASSWORD[A-Z0-9_]*"',
            '"variable":"REDACTED_NAME"',
            stripped,
        )
        if password_like.search(stripped):
            errors.append("possible embedded secret value detected in document payload")
    return errors


def _assert_scope_safe(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    text = json.dumps(payload).lower()
    # References as exclusions are allowed; required-package-content is not.
    for tree in EXCLUDED_PACKAGE_TREES:
        needle = tree.lower()
        if needle not in text:
            continue
        # Look for required-inclusion patterns near the tree name.
        if re.search(
            rf"(required|must[_ ]include|package[_ ]member).{{0,80}}{re.escape(needle)}",
            text,
        ) or re.search(
            rf"{re.escape(needle)}.{{0,80}}(required|must[_ ]include|package[_ ]member)",
            text,
        ):
            errors.append(f"document requires excluded tree as package content: {tree}")
    return errors


def _canonical_sha256(payload: dict[str, Any]) -> str:
    payload_for_hash = dict(payload)
    payload_for_hash["sha256"] = ""
    canonical = json.dumps(payload_for_hash, indent=2, sort_keys=True) + "\n"
    return _sha256_bytes(canonical.encode("utf-8"))


def _chmod_restrictive(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = _canonical_sha256(payload)
    payload["sha256"] = digest
    final = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".partial",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(final.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _chmod_restrictive(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return digest


def verify_document_payload_checksum(payload: dict[str, Any]) -> list[str]:
    """Fail closed when embedded sha256 does not match canonical payload."""
    embedded = str(payload.get("sha256") or "").strip()
    if not embedded:
        return ["document missing embedded sha256"]
    expected = _canonical_sha256(payload)
    if embedded != expected:
        return [f"document checksum mismatch: embedded={embedded} expected={expected}"]
    return []


def required_evidence_errors(
    mount_root: Path,
    *,
    run_id: str,
    mercury_capture_id: str,
    erebus_capture_id: str,
) -> list[str]:
    """Fail closed when required Phase 3B / capture evidence is missing."""
    errors: list[str] = []
    phase = mount_root / CONTROL_DIRNAME / "phase3b" / run_id
    required = [
        phase / "phase3b_summary.json",
        phase / "preflight" / "preflight.json",
        phase / "dumps" / "dump_metadata.json",
        mount_root
        / CONTROL_DIRNAME
        / "validation"
        / "mercury"
        / mercury_capture_id
        / "capture_identity.json",
        mount_root
        / CONTROL_DIRNAME
        / "validation"
        / "erebus"
        / erebus_capture_id
        / "capture_summary.json",
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"required evidence missing: {path}")
    return errors


def _header(
    *,
    document_id: str,
    run_id: str,
    mercury_commit: str,
    mercury_capture_id: str,
    erebus_commit: str,
    erebus_capture_id: str,
    evidence_refs: list[str],
    body: dict[str, Any],
    linked_preview_id: str = LINKED_PREVIEW_ID,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    unresolved = _count_unresolved(body)
    return {
        "schema": DOCUMENT_SCHEMA,
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "document_id": document_id,
        "filename": DOCUMENT_FILENAMES[document_id],
        "generated_at_utc": generated_at,
        "source_run_id": run_id,
        "linked_preview_id": linked_preview_id,
        "mercury_commit": mercury_commit,
        "mercury_capture_id": mercury_capture_id,
        "erebus_commit": erebus_commit,
        "erebus_capture_id": erebus_capture_id,
        "sha256": "",
        "unresolved_field_count": unresolved,
        "generated_by": {
            "tool": "mercury.migration.destination_documents",
            "host": socket.gethostname(),
            "pid": os.getpid(),
        },
        "evidence_refs": evidence_refs,
        "body": body,
    }


def _erebus_env_key_names_from_example(text: str) -> list[str]:
    keys: list[str] = []
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if not s or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]+", key):
            keys.append(key)
    # Preserve order, unique.
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def build_document_bodies(
    mount_root: Path,
    *,
    run_id: str,
    mercury_commit: str,
    mercury_capture_id: str,
    erebus_commit: str,
    erebus_capture_id: str,
    policy: RetentionPolicy,
) -> dict[str, tuple[dict[str, Any], list[str]]]:
    """Return document_id -> (body, evidence_refs)."""
    phase = mount_root / CONTROL_DIRNAME / "phase3b" / run_id
    mercury_cap = (
        mount_root / CONTROL_DIRNAME / "validation" / "mercury" / mercury_capture_id
    )
    erebus_cap = (
        mount_root / CONTROL_DIRNAME / "validation" / "erebus" / erebus_capture_id
    )
    phase_summary = _load_json(phase / "phase3b_summary.json") or {}
    preflight = _load_json(phase / "preflight" / "preflight.json") or {}
    mercury_identity = _load_json(mercury_cap / "capture_identity.json") or {}
    erebus_summary = _load_json(erebus_cap / "capture_summary.json") or {}
    storage_identity = _load_json(mount_root / CONTROL_DIRNAME / "storage_identity.json") or {}
    dump_meta = _load_json(phase / "dumps" / "dump_metadata.json") or {}
    checksum_refs = _load_json(phase / "dumps" / "checksum_refs.txt")  # may be json
    if checksum_refs is None:
        # checksum_refs.txt is JSON per earlier inspection
        try:
            checksum_refs = json.loads(
                (phase / "dumps" / "checksum_refs.txt").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            checksum_refs = {}

    erebus_dump = (dump_meta.get("dumps") or {}).get("erebus_threat_intel_prod") or {}
    android_dump = (dump_meta.get("dumps") or {}).get("android_permission_intel") or {}
    erebus_manifest = erebus_dump.get("manifest") or {}
    android_manifest = android_dump.get("manifest") or {}

    intake_contract_sha = (
        ((erebus_summary.get("intake_contract") or {}).get("sha256"))
        or "da10a5e5ed580645f763e149487426a499830f1d6702722ed93757bb69285982"
    )

    env_example = erebus_cap / "ops" / "deps" / ".env.example"
    erebus_env_keys: list[str] = []
    if env_example.is_file():
        erebus_env_keys = _erebus_env_key_names_from_example(
            env_example.read_text(encoding="utf-8", errors="replace")
        )

    mercury_tree = str(mercury_identity.get("tree") or "")
    erebus_tree = str((erebus_summary.get("repository") or {}).get("tree") or "")
    mercury_short = mercury_commit[:7] if mercury_commit else "unknown"
    mercury_bundle_dir = mercury_cap / "bundle"
    mercury_bundle_name = UNRESOLVED
    mercury_bundle_prereq_short = UNRESOLVED
    mercury_bundle_prereq_full = UNRESOLVED
    _known_prereq = {
        "2596b85": "2596b8588c868a68d661dfaae23a5609cc77279a",
        "31ebc49": "31ebc49c8c6a5ecbb012b1f4c1963f6114985092",
    }
    if mercury_bundle_dir.is_dir():
        preferred = sorted(mercury_bundle_dir.glob("mercury_main_*_from_*.bundle"))
        matching = [p for p in preferred if mercury_short in p.name]
        any_bundles = matching or preferred or sorted(mercury_bundle_dir.glob("*.bundle"))
        if any_bundles:
            chosen = any_bundles[0]
            mercury_bundle_name = chosen.name
            if "_from_" in chosen.stem:
                mercury_bundle_prereq_short = chosen.stem.split("_from_", 1)[1]
                mercury_bundle_prereq_full = _known_prereq.get(
                    mercury_bundle_prereq_short, UNRESOLVED
                )
    mercury_bundle_rel = (
        f".mercury_control/validation/mercury/{mercury_capture_id}/bundle/"
        f"{mercury_bundle_name}"
    )
    erebus_bundle_rel = (
        f".mercury_control/validation/erebus/{erebus_capture_id}/"
        f"git/erebus-engine-fedora_3f1bb5b.bundle"
    )

    package_exclusions = sorted(
        set(policy.exclude_from_destination_by_default) | set(policy.manual_review_roots)
    )

    # --- 1) source_host_inventory ---
    source_body = {
        "purpose": (
            "Inventory the sealed source host baseline and destination system "
            "requirements derived from Phase 3B / capture evidence."
        ),
        "source_host": {
            "hostname": phase_summary.get("host") or preflight.get("host") or UNRESOLVED,
            "observed_os": {
                "distribution": "Fedora Linux",
                "version_id_observed_at_document_generation": "43",
                "pretty_name_observed": "Fedora Linux 43 (Workstation Edition)",
                "note": (
                    "Observed on the source workstation at document generation; "
                    "destination must meet Mercury Fedora baseline, not necessarily identical VERSION_ID."
                ),
            },
            "architecture": "x86_64",
            "mariadb_version_observed": preflight.get("mariadb_version")
            or "10.11.18-MariaDB",
            "python_version_observed_at_document_generation": "3.14.6",
            "active_storage": {
                "mount_path": str(mount_root),
                "filesystem_uuid": storage_identity.get("filesystem_uuid")
                or DEFAULT_PRIMARY_UUID,
                "filesystem_label": storage_identity.get("filesystem_label")
                or "MERCURY_DATA_V2",
                "filesystem_type": storage_identity.get("filesystem_type") or "ext4",
                "active_write_role_at_phase3b": preflight.get("active_write_role"),
                "migration_state_at_phase3b": preflight.get("migration_state"),
                "free_space_at_phase3b_df_h": (preflight.get("df_h") or "").strip()
                or UNRESOLVED,
            },
            "phase3b_readiness_status": phase_summary.get("readiness_status"),
            "destination_cutover_started": phase_summary.get(
                "destination_cutover_started", False
            ),
        },
        "destination_system_requirements": {
            "supported_os_baseline": "Fedora Linux (Mercury production target); exact VERSION_ID is UNRESOLVED_OPERATOR_INPUT pending destination host selection",
            "architecture": "x86_64",
            "required_packages": [
                {
                    "name": "git",
                    "purpose": "bundle verify / clone / checkout",
                    "evidence": "docs/system_deployment_runbook.md",
                },
                {
                    "name": "mariadb / mariadb-server client+server tooling",
                    "purpose": "restore-check and doctor connectivity",
                    "evidence": "Phase 3B preflight mariadb_version; docs/system_deployment_runbook.md",
                },
                {
                    "name": "python3 >= 3.12 with venv",
                    "purpose": "Mercury runtime (pyproject requires-python >=3.12)",
                    "evidence": "pyproject.toml",
                },
                {
                    "name": "optional pymysql (pip extra [mariadb])",
                    "purpose": "TCP MariaDB mode when use_client=false",
                    "evidence": "pyproject.toml optional-dependencies",
                },
                {
                    "name": "exact dnf package set for destination image",
                    "purpose": "operator-confirmed package baseline",
                    "value": UNRESOLVED,
                },
            ],
            "python": {
                "requires_python": ">=3.12",
                "venv_required": True,
                "install_order": [
                    "python3 -m venv .venv",
                    "source .venv/bin/activate",
                    'pip install -e ".[mariadb,dev]"',
                ],
                "evidence": "AGENTS.md / pyproject.toml",
            },
            "mariadb_compatibility": {
                "source_observed": preflight.get("mariadb_version") or "10.11.18-MariaDB",
                "destination_required_minimum": UNRESOLVED,
                "note": "Rehearsal dumps produced with mariadb-dump on source; destination must restore those dumps into disposable schemas only.",
            },
            "filesystem_and_mounts": {
                "operator_storage_label": "MERCURY_DATA_V2",
                "operator_storage_uuid": DEFAULT_PRIMARY_UUID,
                "filesystem_type": "ext4",
                "mount_path_canonical": "/mnt/MERCURY_DATA_V2",
                "destination_mount_path": UNRESOLVED,
                "writable_required_for_rehearsal_artifacts": True,
            },
            "required_system_users_and_groups": {
                "mercury_operator_account": UNRESOLVED,
                "mariadb_unix_socket_peer_user": UNRESOLVED,
                "documented_db_account_names": [
                    "root (socket auth example in config/local.example.toml)",
                    "mercury_readonly (TCP password_env example)",
                    "erebus_app@localhost (Phase 3B restorecheck grants warning — name only)",
                    "erebus_operator (Erebus .env.example grant commentary — name only)",
                ],
            },
            "required_directories_and_permissions": {
                "mercury_checkout": UNRESOLVED,
                "erebus_checkout": UNRESOLVED,
                "operator_storage_mount": UNRESOLVED,
                "erebus_intake_root_name": "erebus-intake",
                "permission_expectation": (
                    "Operator must own checkout trees; operator storage mount must match UUID "
                    f"{DEFAULT_PRIMARY_UUID} before writes; never package secret files."
                ),
            },
            "ports_and_firewall": {
                "mariadb_port_default": 3306,
                "additional_ports": UNRESOLVED,
                "firewall_policy": UNRESOLVED,
            },
            "required_free_disk_space": {
                "package_estimate_gib": 0.34,
                "phase3b_source_available_at_preflight": "~593 GiB available on MERCURY_DATA_V2",
                "destination_free_space_required_gib": UNRESOLVED,
                "note": "Destination must hold package (~0.34 GiB) plus restore workspace and MariaDB data growth; exact floor is operator input.",
            },
            "expected_host_identity_fields": [
                "hostname",
                "os pretty name / VERSION_ID",
                "uname -m",
                "MariaDB version string",
                "Python version",
                "operator storage UUID + mount path",
                "Mercury commit/capture after reconstruction",
                "Erebus commit/capture after reconstruction",
            ],
        },
        "package_scope_exclusions_confirmed": package_exclusions,
    }
    source_refs = [
        str(phase / "phase3b_summary.json"),
        str(phase / "preflight" / "preflight.json"),
        str(mount_root / CONTROL_DIRNAME / "storage_identity.json"),
        str(mercury_cap / "capture_identity.json"),
        "config/local.example.toml",
        "pyproject.toml",
        "docs/system_deployment_runbook.md",
    ]

    # --- 2) environment_secret_name_inventory ---
    secret_body = {
        "purpose": (
            "Inventory secret *names* and provisioning paths only. "
            "Never package values, tokens, private keys, or credential hashes."
        ),
        "never_package": [
            "passwords",
            "tokens",
            "private keys",
            "API keys (values)",
            "credential hashes",
            ".env files with populated secrets",
            "config/local.toml when it contains password material",
        ],
        "mercury": {
            "config_files": [
                {
                    "path": "config/local.toml",
                    "packaged": False,
                    "notes": "gitignored; create via mercury config init on destination",
                },
                {
                    "path": "config/local.example.toml",
                    "packaged": False,
                    "notes": "template only; may travel with Mercury source reconstruction",
                },
            ],
            "env_vars": [
                {
                    "name": "MERCURY_MARIADB_PASSWORD",
                    "purpose": "TCP MariaDB password when password_env is set",
                    "value_source": "manual",
                    "must_never_package": True,
                },
                {
                    "name": "MERCURY_PRIMARY_MOUNT",
                    "purpose": "override primary mount path",
                    "value_source": "manual_or_config",
                    "must_never_package": False,
                },
                {
                    "name": "MERCURY_LEGACY_MOUNT",
                    "purpose": "override legacy/USB mount path",
                    "value_source": "manual_or_config",
                    "must_never_package": False,
                },
                {
                    "name": "MERCURY_BACKUP_ROOT",
                    "purpose": "override backup root",
                    "value_source": "manual_or_config",
                    "must_never_package": False,
                },
            ],
            "mariadb_account_names": [
                "root",
                "mercury_readonly",
            ],
            "local_toml_keys_non_secret": [
                "mode",
                "backup_root",
                "log_dir",
                "dry_run",
                "live_actions_enabled",
                "host",
                "port",
                "user",
                "use_client",
                "unix_socket",
                "password_env",
                "active_write_role",
                "migration_state",
                "filesystem_uuid",
                "mount_path",
            ],
            "values_entered_manually": [
                "MERCURY_MARIADB_PASSWORD (if TCP auth)",
                "any destination-specific mount paths not matching source",
                UNRESOLVED,
            ],
            "permission_expectations": [
                "config/local.toml mode 0600 recommended when password_env unused but secrets present",
                ".env files mode 0600",
                "never world-readable",
            ],
        },
        "erebus": {
            "env_example_path_in_capture": str(env_example.relative_to(mount_root))
            if env_example.is_file()
            else UNRESOLVED,
            "env_variable_names": erebus_env_keys
            or [
                "EREBUS_DB_NAME",
                "EREBUS_PERMISSION_INTEL_DB_NAME",
                "EREBUS_DB_HOST",
                "EREBUS_DB_PORT",
                "EREBUS_DB_USER",
                "EREBUS_DB_PASSWORD",
                "EREBUS_PERMISSION_INTEL_DB_HOST",
                "EREBUS_PERMISSION_INTEL_DB_PORT",
                "EREBUS_PERMISSION_INTEL_DB_USER",
                "EREBUS_PERMISSION_INTEL_DB_PASSWORD",
                "EREBUS_MALWAREBAZAAR_AUTH_KEY",
            ],
            "api_key_variable_names": [
                "EREBUS_MALWAREBAZAAR_AUTH_KEY",
            ],
            "dangerous_bypass_names_never_enable_in_prod": [
                "EREBUS_INTAKE_SKIP_MOUNT_GUARD",
            ],
            "values_entered_manually": [
                "EREBUS_DB_PASSWORD",
                "EREBUS_PERMISSION_INTEL_DB_PASSWORD",
                "EREBUS_MALWAREBAZAAR_AUTH_KEY",
                "any VT or third-party keys not listed in capture example",
                UNRESOLVED,
            ],
            "must_never_package": True,
        },
        "file_paths_for_secret_provisioning": [
            {
                "path": "<mercury_checkout>/config/local.toml",
                "status": UNRESOLVED,
            },
            {
                "path": "<erebus_checkout>/.env",
                "status": UNRESOLVED,
            },
        ],
    }
    secret_refs = [
        "config/local.example.toml",
        str(env_example) if env_example.is_file() else "erebus capture ops/deps/.env.example",
        str(erebus_cap / "runtime_restrictions.json"),
        "src/mercury/migration/readiness.py",
    ]

    # --- 3) destination_acceptance_checklist ---
    accept_body = {
        "purpose": (
            "Ordered acceptance checks for destination restore rehearsal. "
            "Final cutover is NOT declared complete by this document."
        ),
        "final_cutover_complete": False,
        "rehearsal_only": True,
        "prohibit_restore_over_live_destination_production_schemas": True,
        "repository_reconstruction": {
            "mercury": {
                "commit": mercury_commit,
                "capture_id": mercury_capture_id,
                "tree": mercury_tree or UNRESOLVED,
                "repository_url": mercury_identity.get("repository_url")
                or "https://github.com/kevin-ch-day/Mercury.git",
                "branch": mercury_identity.get("branch") or "main",
                "bundle_path_relative": mercury_bundle_rel,
                "bundle_prerequisite_short": mercury_bundle_prereq_short,
                "bundle_prerequisite_commit": mercury_bundle_prereq_full,
                "bundle_verify_commands": [
                    f"git bundle verify {mercury_bundle_rel}",
                    f"git clone --no-checkout <seed_repo_containing_{mercury_bundle_prereq_short}> /tmp/mercury_recon",
                    f"git -C /tmp/mercury_recon checkout --detach {mercury_bundle_prereq_full}",
                    f"git -C /tmp/mercury_recon fetch {mercury_bundle_rel} HEAD:refs/heads/rebased-main",
                    "git -C /tmp/mercury_recon checkout rebased-main",
                    f"test $(git -C /tmp/mercury_recon rev-parse HEAD) = {mercury_commit}",
                    f"test $(git -C /tmp/mercury_recon rev-parse HEAD^{{tree}}) = {mercury_tree or UNRESOLVED}",
                ],
                "clean_worktree_check": "git status --porcelain must be empty after checkout",
                "dependency_install_order": [
                    "python3 -m venv .venv",
                    "source .venv/bin/activate",
                    'pip install -e ".[mariadb,dev]"',
                ],
            },
            "erebus": {
                "commit": erebus_commit,
                "capture_id": erebus_capture_id,
                "tree": erebus_tree or UNRESOLVED,
                "repository_url": (erebus_summary.get("repository") or {}).get("url")
                or "https://github.com/kevin-ch-day/erebus-engine-fedora.git",
                "branch": (erebus_summary.get("repository") or {}).get("branch") or "main",
                "bundle_path_relative": erebus_bundle_rel,
                "bundle_verify_commands": [
                    f"git bundle verify {erebus_bundle_rel}",
                    "git clone --mirror / empty repo then git pull <bundle> OR git clone <bundle> when complete history",
                    f"test $(git rev-parse HEAD) = {erebus_commit}",
                    f"test $(git rev-parse HEAD^{{tree}}) = {erebus_tree or UNRESOLVED}",
                ],
                "clean_worktree_check": "git status --porcelain must be empty",
                "dependency_install_order": UNRESOLVED,
            },
        },
        "database_restore_rehearsal": {
            "pinned_backup_ids": list(policy.protected_backup_ids),
            "restore_order": [
                "erebus_threat_intel_prod-full-20260722_055507_238",
                "android_permission_intel-full-20260722_055648_287",
            ],
            "dumps": [
                {
                    "backup_id": "erebus_threat_intel_prod-full-20260722_055507_238",
                    "database": "erebus_threat_intel_prod",
                    "directory": erebus_dump.get("directory")
                    or str(
                        mount_root
                        / "mercury_backups/2026-07-22/erebus_threat_intel_prod/20260722_055507_238"
                    ),
                    "dump_file": erebus_manifest.get("dump_file"),
                    "schema_file": erebus_manifest.get("schema_file"),
                    "sha256": erebus_manifest.get("sha256")
                    or (checksum_refs or {}).get("erebus_manifest_sha256_field"),
                    "schema_sha256": erebus_manifest.get("schema_sha256"),
                    "size_bytes": erebus_manifest.get("size_bytes"),
                },
                {
                    "backup_id": "android_permission_intel-full-20260722_055648_287",
                    "database": "android_permission_intel",
                    "directory": android_dump.get("directory")
                    or str(
                        mount_root
                        / "mercury_backups/2026-07-22/android_permission_intel/20260722_055648_287"
                    ),
                    "dump_file": android_manifest.get("dump_file"),
                    "schema_file": android_manifest.get("schema_file"),
                    "sha256": android_manifest.get("sha256")
                    or (checksum_refs or {}).get("android_manifest_sha256_field"),
                    "schema_sha256": android_manifest.get("schema_sha256"),
                    "size_bytes": android_manifest.get("size_bytes"),
                },
            ],
            "disposable_destination_schema_naming": [
                "_restorecheck_erebus_threat_intel_prod_<destination_rehearsal_id>",
                "_restorecheck_android_permission_intel_<destination_rehearsal_id>",
            ],
            "source_phase3b_restorecheck_schemas_retained": phase_summary.get(
                "restore_schemas_retained"
            ),
            "source_checkpoint": {
                "phase3b_run_id": run_id,
                "zero_unexplained_restore_differences": phase_summary.get(
                    "zero_unexplained_restore_differences"
                ),
                "comparison_artifact": str(
                    phase / "restore" / "source_vs_restore_comparison.json"
                ),
            },
            "expected_queue_state": (
                "Phase 3B warning: Queue row PROCESSING=1 remained after pause "
                "(no live worker); preserved in dump. Destination rehearsal must not "
                "treat that as a live worker signal."
            ),
            "restore_comparator_procedure": [
                "Restore dumps only into disposable _restorecheck_* schemas",
                "Run Mercury/Erebus source-versus-restore comparator against Phase 3B evidence expectations",
                "Record destination acceptance evidence; do not DROP source Phase 3B restorecheck schemas",
            ],
            "database_doctor_procedure": [
                "./run.sh doctor",
                "./run.sh db ping",
                "Confirm read-only SQL only during discovery",
            ],
            "explicit_prohibitions": [
                "Do not restore over live destination production schemas during rehearsal",
                "Do not drop, overwrite, or restore into *_prod on destination as cutover",
                "Do not use unqualified latest backup IDs",
            ],
        },
        "intake_reconstruction": {
            "destination_path": UNRESOLVED,
            "intake_root_name": "erebus-intake",
            "include_only": [
                "intake_contract.json",
                "README.md",
                "manifests/",
                "ingest_ready/",
                "prepared/",
                "notes/",
            ],
            "exclude_explicitly": [
                "downloads/",
                "archive/",
                "logs/",
                "tools/",
            ],
            "intake_contract_sha256": intake_contract_sha,
            "checksum_validation": "Verify intake_contract.json SHA-256 matches capture; validate allowlisted subtree file manifests where present",
            "mount_identity_checks": [
                f"Confirm operator storage UUID {DEFAULT_PRIMARY_UUID} before intake writes",
                "Never set EREBUS_INTAKE_SKIP_MOUNT_GUARD on production paths",
            ],
            "ownership_and_permissions": UNRESOLVED,
            "acceptance_test_files": [
                "erebus-intake/intake_contract.json",
                "erebus capture artifacts/intake_contract/intake_contract.json",
            ],
        },
        "validation_checks": {
            "package_checksum_verification": "Verify package SHA-256 manifest after future create",
            "git_reconstruction_verification": "HEAD and tree hashes must match captures",
            "database_restore_checks": "Disposable schema restore + object counts",
            "erebus_restore_comparator": "Independent comparator PASS evidence remains Phase 3B-linked; re-run on destination rehearsal schemas",
            "doctor_checks": "./run.sh doctor must be non-blocking for rehearsal prerequisites",
            "intake_checks": "Allowlisted subset present; excluded trees absent from package",
            "source_versus_destination_comparison": "Compare destination rehearsal restore to Phase 3B comparison baseline",
            "pass_fail_criteria": [
                "Exact backup IDs restored",
                "Exact Git commits/trees reconstructed",
                "No unqualified latest",
                "No Scytale/ObsidianDroid project trees in package",
                "Zero unexplained restore differences for rehearsal scope OR documented accepted deltas",
                "Source host preserved unchanged as rollback",
            ],
        },
        "package_exclusions": package_exclusions
        + [
            "erebus-intake/downloads",
            "erebus-intake/archive",
            "erebus-intake/logs",
            "erebus-intake/tools",
            "routine development backups",
            "ordinary logs",
            "old transfer packages",
            "USB archive contents",
            "quarantine contents",
        ],
    }
    accept_refs = [
        str(phase / "phase3b_summary.json"),
        str(phase / "dumps" / "dump_metadata.json"),
        str(phase / "restore" / "source_vs_restore_comparison.json"),
        str(mercury_cap / "capture_identity.json"),
        str(erebus_cap / "capture_summary.json"),
        str(
            mount_root
            / "mercury_backups/2026-07-22/erebus_threat_intel_prod/20260722_055507_238/manifest.json"
        ),
        str(
            mount_root
            / "mercury_backups/2026-07-22/android_permission_intel/20260722_055648_287/manifest.json"
        ),
    ]

    # --- 4) rollback_instructions ---
    rollback_body = {
        "purpose": (
            "Keep the source host authoritative until destination acceptance is recorded. "
            "No destination cutover is authorized by package creation alone."
        ),
        "source_host_preservation_rule": (
            "Source host and Phase 3B evidence remain unchanged and available as rollback. "
            "Do not clean, quarantine, or drop Phase 3B restorecheck schemas without explicit approval."
        ),
        "final_cutover_complete": False,
        "rollback_triggers": [
            "Destination Git reconstruction mismatch",
            "Destination restore rehearsal failure",
            "Package checksum failure",
            "Operator abort before acceptance evidence recorded",
        ],
        "rollback_procedure": [
            "Stop destination rehearsal writes",
            "Do not promote disposable _restorecheck_* schemas to production names",
            "Retain destination failure evidence under a dated rehearsal folder",
            "Resume operations on the source host using existing Phase 3B / capture identities",
            "Do not delete origin/agent/storage-cutover-readiness safety copy until later verification",
            "Cleanup remains LOCKED_PENDING_DESTINATION_VALIDATION",
        ],
        "package_checksum_verification": [
            "After future package create: sha256sum -c package SHA256SUMS",
            "Confirm approved preview ID matches package receipt",
        ],
        "git_reconstruction_verification": [
            f"Mercury HEAD == {mercury_commit}",
            f"Erebus HEAD == {erebus_commit}",
        ],
        "database_restore_checks": [
            "Only disposable schemas",
            "Pinned Phase 3B backup IDs only",
        ],
        "pass_fail_criteria": accept_body["validation_checks"]["pass_fail_criteria"],
        "execution_order_reference": [
            "1. Verify transfer package on source",
            "2. Transfer package to destination",
            "3. Verify package on destination",
            "4. Install destination prerequisites",
            "5. Reconstruct Mercury",
            "6. Reconstruct Erebus",
            "7. Provision configuration and secrets",
            "8. Configure Mercury storage contract",
            "9. Restore Phase 3B dumps into disposable schemas",
            "10. Run source-versus-restore validation",
            "11. Validate Erebus intake subset",
            "12. Run doctor and comparator checks",
            "13. Record destination acceptance evidence",
            "14. Keep source host unchanged as rollback",
        ],
        "package_creation_safety_checks_required_before_create": [
            "validate active HDD UUID",
            "load exact approved preview ID",
            "verify preview checksum",
            "verify every source artifact unchanged",
            "refuse unqualified latest",
            "refuse new package members not listed in preview",
            "refuse ScytaleDroid or ObsidianDroid project trees",
            "create package atomically where possible",
            "produce package SHA-256 manifest",
            "verify completed package",
            "write package receipt",
            "preserve Phase 3B evidence unchanged",
        ],
    }
    rollback_refs = [
        str(phase / "PHASE3B_REPORT.md"),
        str(phase / "phase3b_summary.json"),
        str(mercury_cap / "capture_identity.json"),
        "docs/disaster_recovery_runbook.md",
    ]

    return {
        "source_host_inventory": (source_body, source_refs),
        "environment_secret_name_inventory": (secret_body, secret_refs),
        "destination_acceptance_checklist": (accept_body, accept_refs),
        "rollback_instructions": (rollback_body, rollback_refs),
    }


def generate_destination_documents(
    mount_root: Path,
    *,
    run_id: str = "20260722T055400Z_phase3b",
    mercury_commit: str = "2596b8588c868a68d661dfaae23a5609cc77279a",
    mercury_capture_id: str = "mercury_destination_candidate_2596b85_20260722T180435Z",
    erebus_commit: str = "3f1bb5bd2229d98b9b76b9f1615238792f12a0b3",
    erebus_capture_id: str = "erebus_destination_candidate_3f1bb5b_20260722T150930Z",
    policy: RetentionPolicy | None = None,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    documents_run: str | None = None,
    linked_preview_id: str = LINKED_PREVIEW_ID,
    overwrite_legacy_documents: bool = False,
) -> DocumentGenerationResult:
    """Write the four destination documents under a versioned governed path.

    By default writes to ``documents_runs/<stamp>/`` and never overwrites the
    historical ``documents/`` tree unless ``overwrite_legacy_documents`` is set
    (tests only).
    """
    policy = policy or load_retention_policy()
    stamp = documents_run or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if overwrite_legacy_documents:
        out_dir = legacy_documents_dir(mount_root, run_id)
        stamp = "legacy"
    else:
        out_dir = documents_dir_for(mount_root, run_id, documents_run=stamp)
    result = DocumentGenerationResult(
        run_id=run_id,
        documents_dir=out_dir,
        linked_preview_id=linked_preview_id,
    )
    validation = validate_storage_mount(
        mount_path=mount_root,
        expected_uuid=expected_uuid,
        expected_fstype="ext4",
        require_writable=True,
    )
    result.mount_uuid_ok = validation.ok
    if not validation.ok:
        result.errors.append(
            validation.blocker or f"mount validation failed: {validation.code}"
        )
        return result

    evidence_errors = required_evidence_errors(
        mount_root,
        run_id=run_id,
        mercury_capture_id=mercury_capture_id,
        erebus_capture_id=erebus_capture_id,
    )
    if evidence_errors:
        result.errors.extend(evidence_errors)
        return result

    # Refuse to clobber historical documents/ unless explicitly allowed.
    if not overwrite_legacy_documents and out_dir == legacy_documents_dir(mount_root, run_id):
        result.errors.append("refusing to overwrite historical documents/; use documents_runs")
        return result

    bodies = build_document_bodies(
        mount_root,
        run_id=run_id,
        mercury_commit=mercury_commit,
        mercury_capture_id=mercury_capture_id,
        erebus_commit=erebus_commit,
        erebus_capture_id=erebus_capture_id,
        policy=policy,
    )
    result.documents_dir.mkdir(parents=True, exist_ok=True)

    for doc_id in DOCUMENT_IDS:
        body, refs = bodies[doc_id]
        payload = _header(
            document_id=doc_id,
            run_id=run_id,
            mercury_commit=mercury_commit,
            mercury_capture_id=mercury_capture_id,
            erebus_commit=erebus_commit,
            erebus_capture_id=erebus_capture_id,
            evidence_refs=refs,
            body=body,
            linked_preview_id=linked_preview_id,
        )
        payload["documents_run"] = stamp
        scope_errors = _assert_scope_safe(payload)
        secret_errors = _assert_no_secret_values(payload)
        if scope_errors or secret_errors:
            result.errors.extend(scope_errors)
            result.errors.extend(secret_errors)
            continue
        path = out_dir / DOCUMENT_FILENAMES[doc_id]
        digest = _atomic_write_json(path, payload)
        written = _load_json(path) or payload
        result.documents.append(
            DestinationDocument(
                document_id=doc_id,
                path=path,
                sha256=digest,
                unresolved_field_count=int(written.get("unresolved_field_count") or 0),
                payload=written,
            )
        )

    index = {
        "schema": "mercury.destination_documents_index.v1",
        "source_run_id": run_id,
        "documents_run": stamp,
        "linked_preview_id": linked_preview_id,
        "mercury_commit": mercury_commit,
        "mercury_capture_id": mercury_capture_id,
        "erebus_commit": erebus_commit,
        "erebus_capture_id": erebus_capture_id,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "documents": [
            {
                "document_id": d.document_id,
                "path": str(d.path),
                "sha256": d.sha256,
                "unresolved_field_count": d.unresolved_field_count,
            }
            for d in result.documents
        ],
        "errors": list(result.errors),
        "sha256": "",
    }
    index_path = result.documents_dir / "documents_index.json"
    _atomic_write_json(index_path, index)

    lines: list[str] = []
    for path in sorted(result.documents_dir.glob("*.json")):
        lines.append(f"{_sha256_file(path)}  {path.name}")
    sums_path = result.documents_dir / "SHA256SUMS"
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _chmod_restrictive(sums_path)
    return result


def load_destination_documents(
    mount_root: Path,
    run_id: str,
    *,
    documents_run: str | None = None,
) -> dict[str, DestinationDocument]:
    """Load schema-valid documents from the active generation directory."""
    found: dict[str, DestinationDocument] = {}
    docs_dir = resolve_active_documents_dir(
        mount_root, run_id, documents_run=documents_run
    )
    if docs_dir is None:
        return found
    for doc_id in DOCUMENT_IDS:
        path = docs_dir / DOCUMENT_FILENAMES[doc_id]
        payload = _load_json(path)
        if not payload:
            continue
        if payload.get("schema") != DOCUMENT_SCHEMA:
            continue
        if payload.get("document_id") != doc_id:
            continue
        checksum_errors = verify_document_payload_checksum(payload)
        if checksum_errors:
            # Fail closed: omit broken document so preview treats it unresolved.
            continue
        found[doc_id] = DestinationDocument(
            document_id=doc_id,
            path=path,
            sha256=str(payload.get("sha256") or ""),
            unresolved_field_count=int(payload.get("unresolved_field_count") or 0),
            payload=payload,
        )
    return found


def validate_documents_against_preview_pins(
    documents: dict[str, DestinationDocument],
    *,
    run_id: str,
    mercury_commit: str,
    mercury_capture_id: str,
    erebus_commit: str,
    protected_backup_ids: tuple[str, ...] | list[str],
) -> list[str]:
    """Cross-check documents for internal consistency and package pins."""
    errors: list[str] = []
    for doc_id in DOCUMENT_IDS:
        doc = documents.get(doc_id)
        if doc is None:
            errors.append(f"missing document: {doc_id}")
            continue
        payload = doc.payload
        errors.extend(verify_document_payload_checksum(payload))
        if payload.get("source_run_id") != run_id:
            errors.append(f"{doc_id}: source_run_id mismatch")
        if mercury_commit and payload.get("mercury_commit") != mercury_commit:
            errors.append(f"{doc_id}: mercury_commit mismatch")
        if mercury_capture_id and payload.get("mercury_capture_id") != mercury_capture_id:
            errors.append(f"{doc_id}: mercury_capture_id mismatch")
        if erebus_commit and payload.get("erebus_commit") != erebus_commit:
            errors.append(f"{doc_id}: erebus_commit mismatch")
        errors.extend(_assert_no_secret_values(payload))
        errors.extend(_assert_scope_safe(payload))
        text = json.dumps(payload).lower()
        if re.search(r"\bunqualified latest\b", text) is None and re.search(
            r"backup[^\n]{0,40}\blatest\b|\blatest\b[^\n]{0,40}backup", text
        ):
            errors.append(f"{doc_id}: possible unqualified latest reference")

    accept = documents.get("destination_acceptance_checklist")
    if accept:
        body = accept.payload.get("body") or {}
        dumps = (body.get("database_restore_rehearsal") or {}).get("dumps") or []
        ids = {d.get("backup_id") for d in dumps if isinstance(d, dict)}
        for backup_id in protected_backup_ids:
            if backup_id not in ids:
                errors.append(f"acceptance checklist missing backup id: {backup_id}")
        recon = body.get("repository_reconstruction") or {}
        merc = recon.get("mercury") or {}
        ereb = recon.get("erebus") or {}
        if mercury_commit and merc.get("commit") != mercury_commit:
            errors.append("acceptance checklist mercury commit mismatch")
        if mercury_capture_id and merc.get("capture_id") != mercury_capture_id:
            errors.append("acceptance checklist mercury capture mismatch")
        if erebus_commit and ereb.get("commit") != erebus_commit:
            errors.append("acceptance checklist erebus commit mismatch")
        intake = body.get("intake_reconstruction") or {}
        for required in (
            "intake_contract.json",
            "README.md",
            "manifests/",
            "ingest_ready/",
            "prepared/",
            "notes/",
        ):
            if required not in (intake.get("include_only") or []):
                errors.append(f"intake include missing: {required}")
        for banned in ("downloads/", "archive/", "logs/", "tools/"):
            if banned not in (intake.get("exclude_explicitly") or []):
                errors.append(f"intake exclude missing: {banned}")
        if body.get("final_cutover_complete") is True:
            errors.append("acceptance checklist must not declare cutover complete")
        if body.get("prohibit_restore_over_live_destination_production_schemas") is not True:
            errors.append("acceptance checklist must prohibit prod restore during rehearsal")

    rollback = documents.get("rollback_instructions")
    if rollback:
        body = rollback.payload.get("body") or {}
        if body.get("final_cutover_complete") is True:
            errors.append("rollback instructions must not declare cutover complete")
        if "source host" not in json.dumps(body).lower():
            errors.append("rollback instructions missing source host preservation guidance")

    # Documents must agree with each other on shared pins.
    if documents:
        commits = {d.payload.get("mercury_commit") for d in documents.values()}
        captures = {d.payload.get("mercury_capture_id") for d in documents.values()}
        runs = {d.payload.get("source_run_id") for d in documents.values()}
        erebus_commits = {d.payload.get("erebus_commit") for d in documents.values()}
        if len(commits) != 1:
            errors.append("documents disagree on mercury_commit")
        if len(captures) != 1:
            errors.append("documents disagree on mercury_capture_id")
        if len(runs) != 1:
            errors.append("documents disagree on source_run_id")
        if len(erebus_commits) != 1:
            errors.append("documents disagree on erebus_commit")

    package_blockers = [
        row
        for row in classify_unresolved_fields(documents)
        if row["class"] == "PACKAGE_CREATION_BLOCKER"
        or row["required_before_package_create"] == "yes"
    ]
    for row in package_blockers:
        errors.append(
            f"package-creation-blocking unresolved field: {row['document']}:{row['field']}"
        )
    return errors


def evaluate_package_create_preconditions(
    *,
    preview_id: str | None,
    preview_checksum: str | None,
    expected_preview_checksum: str | None,
    source_artifacts_unchanged: bool,
    members_match_preview: bool,
    uses_unqualified_latest: bool,
    protected_checksum_ok: bool,
    scytale_or_obsidian_present: bool,
    active_hdd_identity_ok: bool,
    documents: dict[str, DestinationDocument] | None = None,
) -> list[str]:
    """Return refusal reasons for package create. Does not create a package."""
    refusals: list[str] = []
    if not preview_id:
        refusals.append("preview ID is missing")
    if not preview_checksum or not expected_preview_checksum:
        refusals.append("preview checksum missing")
    elif preview_checksum != expected_preview_checksum:
        refusals.append("preview checksum differs")
    if not source_artifacts_unchanged:
        refusals.append("a source artifact changed")
    if not members_match_preview:
        refusals.append("a package member is not in the preview")
    if uses_unqualified_latest:
        refusals.append("an unqualified latest appears")
    if not protected_checksum_ok:
        refusals.append("a protected checksum fails")
    if scytale_or_obsidian_present:
        refusals.append("Scytale or Obsidian project data appears")
    if not active_hdd_identity_ok:
        refusals.append("active HDD identity fails")
    if documents is not None:
        for row in classify_unresolved_fields(documents):
            if (
                row["class"] == "PACKAGE_CREATION_BLOCKER"
                or row["required_before_package_create"] == "yes"
            ):
                refusals.append(
                    f"package-creation-blocking unresolved field remains: {row['field']}"
                )
        for doc in documents.values():
            refusals.extend(verify_document_payload_checksum(doc.payload))
    return refusals


# Keep alias used by older callers / tests that imported documents_dir_for as legacy path.
def documents_dir_for_legacy(mount_root: Path, run_id: str) -> Path:
    return legacy_documents_dir(mount_root, run_id)
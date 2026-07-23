"""Governed Backup and Sync session receipts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from mercury.backup.session_models import BackupSyncSession
from mercury.core.storage_roles import CONTROL_DIRNAME
from mercury.storage.host_maintenance import assert_not_live_mercury_path, writes_allowed


SESSION_RECEIPT_DIRNAME = "backup_sync_sessions"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    assert_not_live_mercury_path(tmp, purpose="session receipt write")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def default_session_control_root() -> Path:
    from mercury.core.usb_mount import resolve_operator_mount

    return resolve_operator_mount() / CONTROL_DIRNAME / SESSION_RECEIPT_DIRNAME


def write_session_receipt(
    session: BackupSyncSession,
    *,
    control_root: Path | None = None,
    require_active_operator_mount: bool | None = None,
) -> Path:
    """Write governed session receipt under ``.mercury_control/backup_sync_sessions/``."""
    live_root = control_root is None
    if not writes_allowed():
        raise RuntimeError(
            "refusing to write governed Backup and Sync receipt while Mercury writes are disabled"
        )
    if session.session_result.value in {"REFUSED", "CANCELLED"} and not session.database_artifacts:
        raise RuntimeError(
            "refusing to write governed session receipt for refused/cancelled empty session"
        )

    if control_root is None:
        control_root = default_session_control_root()
    if require_active_operator_mount is None:
        require_active_operator_mount = live_root
    if require_active_operator_mount:
        from mercury.core.usb_mount import assert_operator_storage_path

        assert_operator_storage_path(control_root, action="backup-sync session receipt write")

    directory = control_root / session.session_id
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)

    payload = session.model_dump(mode="json")
    payload.pop("receipt_path", None)
    payload.pop("receipt_sha256", None)
    session_json = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    session_path = directory / "session.json"
    digest = hashlib.sha256(session_json.encode("utf-8")).hexdigest()
    _atomic_write_text(session_path, session_json)
    _atomic_write_text(directory / "session.json.sha256", f"{digest}  session.json\n")

    inventory = {
        "session_id": session.session_id,
        "database_artifacts": [a.model_dump(mode="json") for a in session.database_artifacts],
        "git_artifacts": [a.model_dump(mode="json") for a in session.git_artifacts],
        "sync_artifacts": [a.model_dump(mode="json") for a in session.sync_artifacts],
        "restore_check_artifacts": [
            a.model_dump(mode="json") for a in session.restore_check_artifacts
        ],
        "exact_artifact_ids": list(session.exact_artifact_ids),
    }
    _atomic_write_text(
        directory / "artifact_inventory.json",
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write_text(
        directory / "warnings.json",
        json.dumps(
            {"warnings": session.warnings, "failures": session.failures},
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    plan_payload = (
        session.frozen_plan.model_dump(mode="json")
        if session.frozen_plan is not None
        else session.requested_operations.model_dump(mode="json")
    )
    _atomic_write_text(
        directory / "commands.json",
        json.dumps(
            {
                "session_id": session.session_id,
                "operator_intent": session.operator_intent,
                "frozen_plan": plan_payload,
                "requested_operations": session.requested_operations.model_dump(),
                "storage_transition": session.storage_transition.model_dump(),
                "phase3b_separation_note": session.phase3b_separation_note,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    summary = render_session_summary_text(session)
    _atomic_write_text(directory / "session_summary.txt", summary + "\n")

    # SHA256SUMS over receipt members (excluding itself).
    lines: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.name in {"SHA256SUMS", "SHA256SUMS.partial"}:
            continue
        if not path.is_file():
            continue
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{file_digest}  {path.name}")
    _atomic_write_text(directory / "SHA256SUMS", "\n".join(lines) + "\n")
    return session_path


def write_host_local_session_refusal(
    session: BackupSyncSession,
    *,
    refusal_root: Path | None = None,
) -> Path:
    """Host-local non-governed refusal/cancel record (never under the Mercury HDD)."""
    from mercury.backup.write_preflight import default_host_local_refusal_root

    root = refusal_root or default_host_local_refusal_root()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = root / f"{session.session_id}_refused.json"
    payload = session.model_dump(mode="json")
    payload.update(
        {
            "evidence_class": "host_local_session_refusal",
            "not_backup_evidence": True,
            "not_handoff_evidence": True,
            "governed_hdd_backup_evidence": False,
        }
    )
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _atomic_write_text(path, text)
    _atomic_write_text(path.with_suffix(path.suffix + ".sha256"), f"{digest}  {path.name}\n")
    return path


def render_session_summary_text(session: BackupSyncSession) -> str:
    lines = [
        "BACKUP AND SYNC SESSION",
        "─" * 62,
        f"Session ID: {session.session_id}",
        f"Result:     {session.session_result.value}",
        "",
        "Production databases",
        f"  Selected:  {session.production_backup_result.selected}",
        f"  Written:   {session.production_backup_result.written}",
        f"  Verified:  {session.production_backup_result.verified}",
        f"  Failed:    {session.production_backup_result.failed}",
        "",
        "Development databases",
        f"  Requested: {'Yes' if session.development_backup_result.requested else 'No'}",
        f"  Written:   {session.development_backup_result.written}",
        f"  Verified:  {session.development_backup_result.verified}",
        f"  Failed:    {session.development_backup_result.failed}",
        "",
        "Git recovery",
        f"  Selected:  {session.git_capture_result.selected}",
        f"  Captured:  {session.git_capture_result.written}",
        f"  Verified:  {session.git_capture_result.verified}",
        f"  Failed:    {session.git_capture_result.failed}",
        "",
        "Production → development",
        f"  Requested: {'Yes' if session.production_dev_sync_result.requested else 'No'}",
        f"  Result:    {session.production_dev_sync_result.result.value}",
        "",
        "Restore-check",
        f"  Requested: {'Yes' if session.restore_check_result.requested else 'No'}",
        f"  Result:    {session.restore_check_result.result.value}",
        "",
        f"Artifacts:  {session.artifacts_result}",
        f"Receipt:    {session.receipt_result}",
        f"Overall result: {session.session_result.value}",
        session.phase3b_separation_note,
    ]
    if session.recommended_next_action:
        lines.extend(["", f"Next: {session.recommended_next_action}"])
    return "\n".join(lines)

"""Host-local one-shot authorization receipts for real Erebus capture execute."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .full_suite_policy import ExpectedFailure

SCHEMA = "mercury.erebus_capture.execution_authorization.v1"
CONFIRMATION = "AUTHORIZE EREBUS CAPTURE EXECUTE"


@dataclass(frozen=True)
class ExecutionAuthorization:
    """Validated authorization bound to one exact preview and capture."""

    path: Path
    preview_id: str
    capture_id: str
    mercury_commit: str
    erebus_commit: str
    authorized_at_utc: str
    expires_at_utc: str | None
    approved_full_suite_failures: tuple[ExpectedFailure, ...]
    sha256: str


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_execution_authorization(
    path: Path,
    *,
    preview_id: str,
    capture_id: str | None = None,
    mercury_commit: str | None = None,
    erebus_commit: str | None = None,
    now: datetime | None = None,
) -> ExecutionAuthorization:
    """Load a host-local authorization receipt; refuse incomplete or mismatched pins."""
    if not path.is_file():
        raise ValueError("AUTHORIZATION_MISSING")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("AUTHORIZATION_MALFORMED") from exc
    if not isinstance(data, dict):
        raise ValueError("AUTHORIZATION_MALFORMED")
    if data.get("schema") != SCHEMA or data.get("schema_version") != 1:
        raise ValueError("AUTHORIZATION_SCHEMA")
    if data.get("confirm") != CONFIRMATION:
        raise ValueError("AUTHORIZATION_CONFIRM")
    if data.get("preview_id") != preview_id:
        raise ValueError("AUTHORIZATION_PREVIEW_MISMATCH")
    if capture_id is not None and data.get("capture_id") != capture_id:
        raise ValueError("AUTHORIZATION_CAPTURE_MISMATCH")
    if mercury_commit is not None and data.get("mercury_commit") != mercury_commit:
        raise ValueError("AUTHORIZATION_MERCURY_MISMATCH")
    if erebus_commit is not None and data.get("erebus_commit") != erebus_commit:
        raise ValueError("AUTHORIZATION_EREBUS_MISMATCH")
    if "latest" in json.dumps(data).lower():
        raise ValueError("AUTHORIZATION_LATEST_FORBIDDEN")

    expires = data.get("expires_at_utc")
    if expires:
        try:
            expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("AUTHORIZATION_EXPIRES_INVALID") from exc
        current = now or datetime.now(timezone.utc)
        if current > expiry:
            raise ValueError("AUTHORIZATION_EXPIRED")

    failures: list[ExpectedFailure] = []
    for item in data.get("approved_full_suite_failures") or []:
        if not isinstance(item, dict):
            raise ValueError("AUTHORIZATION_FAILURES_MALFORMED")
        try:
            failures.append(
                ExpectedFailure(
                    node_id=str(item["node_id"]),
                    classification=str(item["classification"]),
                    disposition=str(item.get("disposition", "accepted_unrelated")),
                )
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("AUTHORIZATION_FAILURES_MALFORMED") from exc

    return ExecutionAuthorization(
        path=path,
        preview_id=str(data["preview_id"]),
        capture_id=str(data["capture_id"]),
        mercury_commit=str(data.get("mercury_commit") or ""),
        erebus_commit=str(data.get("erebus_commit") or ""),
        authorized_at_utc=str(data.get("authorized_at_utc") or ""),
        expires_at_utc=str(expires) if expires else None,
        approved_full_suite_failures=tuple(failures),
        sha256=_sha256(path),
    )

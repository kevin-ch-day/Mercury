"""Record HDD SMART health evidence under primary .mercury_control (observe-only)."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.core.storage_roles import CONTROL_DIRNAME

SMART_DIRNAME = "smart"
SMART_LATEST = "hdd_smart_latest.json"


@dataclass(frozen=True)
class SmartHealthResult:
    path: Path
    payload: dict[str, Any]
    executed: bool
    success: bool
    message: str


def _control_smart_dir(config: StorageConfig) -> Path:
    return config.primary.mount_path / CONTROL_DIRNAME / SMART_DIRNAME


def smart_latest_path(*, config: StorageConfig | None = None) -> Path:
    cfg = config or load_storage_config(warn_deprecated=False)
    return _control_smart_dir(cfg) / SMART_LATEST


def resolve_block_device_for_mount(mount_path: Path) -> str | None:
    """Best-effort /dev node for a mount (no sudo)."""
    try:
        completed = subprocess.run(
            ["findmnt", "-no", "SOURCE", "--target", str(mount_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        source = completed.stdout.strip()
        if source.startswith("/dev/"):
            # Strip partition suffix for SMART when possible (sda1 -> sda).
            base = source.rstrip("0123456789")
            if base.startswith("/dev/") and Path(base).exists():
                return base
            return source
    except (OSError, subprocess.CalledProcessError):
        return None
    return None


def read_smart_health_record(*, config: StorageConfig | None = None) -> dict[str, Any] | None:
    path = smart_latest_path(config=config)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def build_smart_health_plan(*, config: StorageConfig | None = None) -> dict[str, Any]:
    cfg = config or load_storage_config(warn_deprecated=False)
    device = resolve_block_device_for_mount(cfg.primary.mount_path)
    smartctl = shutil.which("smartctl")
    return {
        "mount_path": str(cfg.primary.mount_path),
        "filesystem_uuid": cfg.primary.filesystem_uuid,
        "block_device": device,
        "smartctl": smartctl,
        "receipt_path": str(smart_latest_path(config=cfg)),
        "existing": read_smart_health_record(config=cfg),
        "command": (
            f"sudo smartctl -H -i -A {device}" if device else "sudo smartctl -H -i -A <primary-disk>"
        ),
    }


def record_smart_health(
    *,
    config: StorageConfig | None = None,
    runner: subprocess.run | None = None,
) -> SmartHealthResult:
    """Capture SMART health for the primary HDD writer mount.

    Uses sudo smartctl. Writes under primary .mercury_control/smart/ only.
    """
    cfg = config or load_storage_config(warn_deprecated=False)
    from mercury.storage.host_maintenance import refuse_if_hdd_writes_disabled

    try:
        refuse_if_hdd_writes_disabled("SMART health evidence write")
    except RuntimeError as exc:
        plan = build_smart_health_plan(config=cfg)
        return SmartHealthResult(
            Path(plan["receipt_path"]), {}, False, False, str(exc)
        )
    plan = build_smart_health_plan(config=cfg)
    device = plan["block_device"]
    path = Path(plan["receipt_path"])
    if not plan["smartctl"]:
        return SmartHealthResult(path, {}, False, False, "smartctl not found on PATH")
    if not device:
        return SmartHealthResult(path, {}, False, False, "Could not resolve block device for primary mount")

    run = runner or subprocess.run
    health = run(
        ["sudo", "smartctl", "-H", "-i", device],
        check=False,
        capture_output=True,
        text=True,
    )
    # -A can be large; keep attributes optional if health+info succeeded.
    attrs = run(
        ["sudo", "smartctl", "-A", device],
        check=False,
        capture_output=True,
        text=True,
    )
    combined = (health.stdout or "") + "\n" + (attrs.stdout or "")
    passed = "PASSED" in (health.stdout or "") or "PASSED" in combined
    # Return codes: 0 ok; non-zero may still include PASSED with warnings.
    payload: dict[str, Any] = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "mount_path": plan["mount_path"],
        "filesystem_uuid": plan["filesystem_uuid"],
        "block_device": device,
        "smartctl_path": plan["smartctl"],
        "health_exit_code": health.returncode,
        "attributes_exit_code": attrs.returncode,
        "overall_health_passed": passed and not (
            "FAILED" in (health.stdout or "") or "FAILED" in combined
        ),
        "stdout_health": health.stdout or "",
        "stderr_health": health.stderr or "",
        "stdout_attributes": attrs.stdout or "",
        "stderr_attributes": attrs.stderr or "",
    }
    if health.returncode == 1 and "Permission denied" in (health.stderr or ""):
        return SmartHealthResult(
            path,
            payload,
            executed=True,
            success=False,
            message="smartctl permission denied — re-run with an interactive sudo session",
        )
    if "a terminal is required" in (health.stderr or "") or "a password is required" in (health.stderr or ""):
        return SmartHealthResult(
            path,
            payload,
            executed=True,
            success=False,
            message="sudo requires an interactive password — run: " + plan["command"],
        )

    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    mount = cfg.primary.mount_path.expanduser().resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(mount)
    except ValueError as exc:
        raise ValueError(
            f"SMART evidence path is not under primary mount {mount}: {resolved}"
        ) from exc
    if config is None:
        from mercury.core.usb_mount import assert_operator_storage_path

        assert_operator_storage_path(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    ok = bool(payload["overall_health_passed"])
    return SmartHealthResult(
        path,
        payload,
        executed=True,
        success=ok,
        message="SMART health PASSED and recorded." if ok else "SMART capture wrote evidence but health did not PASS.",
    )

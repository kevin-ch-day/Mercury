"""Pinned host-maintenance package selection for safe detach."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.core.storage_roles import CONTROL_DIRNAME, DEFAULT_PRIMARY_LABEL, DEFAULT_PRIMARY_UUID
from mercury.storage.detach_wizard import (
    DETACH_BLOCKED_PACKAGE_NOT_VERIFIED,
    latest_verified_package,
    resolve_detach_package,
    run_detach_wizard,
)
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance


NEW_PKG = "destination_rehearsal_final_source_05f3abc_20260724T185539Z"
OLD_PKG = "destination_rehearsal_final_source_20260723T205343Z"


def _lsblk(*, mountpoint: str) -> dict:
    return {
        "blockdevices": [
            {
                "name": "sdb",
                "path": "/dev/sdb",
                "type": "disk",
                "model": "WDC WD10JDRW-11CFYS0",
                "serial": "WD-TESTSERIAL",
                "children": [
                    {
                        "name": "sdb1",
                        "path": "/dev/sdb1",
                        "pkname": "sdb",
                        "type": "part",
                        "mountpoint": mountpoint,
                        "fstype": "ext4",
                        "label": DEFAULT_PRIMARY_LABEL,
                        "uuid": DEFAULT_PRIMARY_UUID,
                    }
                ],
            }
        ]
    }


def _runner(mountpoint: str):
    def runner(argv, check=False, capture_output=True, text=True):
        if argv[:2] == ["lsblk", "-J"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_lsblk(mountpoint=mountpoint)),
                stderr="",
            )
        if argv and argv[0] == "findmnt":
            return SimpleNamespace(
                returncode=0,
                stdout=f"{DEFAULT_PRIMARY_UUID} {DEFAULT_PRIMARY_LABEL} ext4 {mountpoint}\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return runner


@pytest.fixture
def host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    return path


def _write_package(mount: Path, package_id: str, *, verified: bool = True) -> Path:
    root = mount / CONTROL_DIRNAME / "destination_packages" / package_id
    root.mkdir(parents=True)
    member = root / "payload" / "a.txt"
    member.parent.mkdir(parents=True)
    member.write_text(f"{package_id}\n", encoding="utf-8")
    digest = hashlib.sha256(member.read_bytes()).hexdigest()
    (root / "package_members.sha256").write_text(
        f"{digest}  payload/a.txt\n", encoding="utf-8"
    )
    status = "DESTINATION_PACKAGE_VERIFIED" if verified else "DESTINATION_PACKAGE_FAILED"
    (root / "package_receipt.json").write_text(
        json.dumps({"package_id": package_id, "verification_status": status}),
        encoding="utf-8",
    )
    (root / "verification_report.json").write_text(
        json.dumps({"status": status}), encoding="utf-8"
    )
    return root


def _seed_phase3b(mount: Path) -> None:
    phase = mount / CONTROL_DIRNAME / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    (phase / "phase3b_summary.json").write_text(
        json.dumps({"writers_resumed": False, "run_id": "20260722T055400Z_phase3b"}),
        encoding="utf-8",
    )


def _save_host(path: Path, *, package_id: str) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            destination_rehearsal_in_progress=True,
            package_id=package_id,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=path,
    )


def test_resolve_selects_exact_pinned_package(tmp_path: Path, host_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, NEW_PKG)
    _write_package(mount, OLD_PKG)
    _save_host(host_path, package_id=NEW_PKG)

    pkg_id, status, errors = resolve_detach_package(mount)
    assert errors == []
    assert status == "DESTINATION_PACKAGE_VERIFIED"
    assert pkg_id == NEW_PKG
    # Lexicographic fallback would prefer OLD_PKG; pin must win.
    assert latest_verified_package(mount) == (OLD_PKG, "DESTINATION_PACKAGE_VERIFIED")


def test_resolve_does_not_select_historical_when_new_pinned(
    tmp_path: Path, host_path: Path
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, NEW_PKG)
    _write_package(mount, OLD_PKG)
    _save_host(host_path, package_id=NEW_PKG)

    pkg_id, _, errors = resolve_detach_package(mount)
    assert errors == []
    assert pkg_id == NEW_PKG
    assert pkg_id != OLD_PKG


def test_resolve_missing_pinned_fails_closed(tmp_path: Path, host_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, OLD_PKG)
    _save_host(host_path, package_id=NEW_PKG)

    pkg_id, status, errors = resolve_detach_package(mount)
    assert pkg_id == ""
    assert status == ""
    assert any("pinned package missing" in e and NEW_PKG in e for e in errors)
    # No silent substitution of the historical package.
    assert OLD_PKG not in " ".join(errors)


def test_resolve_unverified_pinned_fails_closed(tmp_path: Path, host_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, NEW_PKG, verified=False)
    _write_package(mount, OLD_PKG, verified=True)
    _save_host(host_path, package_id=NEW_PKG)

    pkg_id, status, errors = resolve_detach_package(mount)
    assert pkg_id == ""
    assert status == ""
    assert any("not DESTINATION_PACKAGE_VERIFIED" in e and NEW_PKG in e for e in errors)


def test_resolve_invalid_pin_does_not_fallback(tmp_path: Path, host_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, OLD_PKG)
    _save_host(host_path, package_id="missing_explicit_package")

    pkg_id, _, errors = resolve_detach_package(mount)
    assert pkg_id == ""
    assert errors
    assert latest_verified_package(mount)[0] == OLD_PKG


def test_resolve_empty_pin_uses_lexicographic_fallback(
    tmp_path: Path, host_path: Path
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, NEW_PKG)
    _write_package(mount, OLD_PKG)
    _save_host(host_path, package_id="")

    pkg_id, status, errors = resolve_detach_package(mount)
    assert errors == []
    assert status == "DESTINATION_PACKAGE_VERIFIED"
    assert pkg_id == OLD_PKG
    assert pkg_id == latest_verified_package(mount)[0]


def test_detach_preview_and_execute_use_same_pinned_package(
    tmp_path: Path, host_path: Path
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, NEW_PKG)
    _write_package(mount, OLD_PKG)
    _seed_phase3b(mount)
    _save_host(host_path, package_id=NEW_PKG)
    mp = str(mount)

    preview = run_detach_wizard(
        execute=False,
        mount=mount,
        runner=_runner(mp),
        skip_log_redirect=True,
        lsblk_json=_lsblk(mountpoint=mp),
    )
    assert preview.package_id == NEW_PKG
    assert any(NEW_PKG in line for phase in preview.phases for line in phase.lines)

    # Execute path resolves package in the same preflight; stop before privileged
    # phases by refusing confirm so package binding is still asserted.
    executed = run_detach_wizard(
        execute=True,
        confirm=None,
        mount=mount,
        runner=_runner(mp),
        skip_log_redirect=True,
        lsblk_json=_lsblk(mountpoint=mp),
    )
    assert executed.package_id == NEW_PKG
    assert executed.package_id == preview.package_id


def test_detach_preview_fails_closed_on_missing_pin(
    tmp_path: Path, host_path: Path
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_package(mount, OLD_PKG)
    _seed_phase3b(mount)
    _save_host(host_path, package_id=NEW_PKG)
    mp = str(mount)

    preview = run_detach_wizard(
        execute=False,
        mount=mount,
        runner=_runner(mp),
        skip_log_redirect=True,
        lsblk_json=_lsblk(mountpoint=mp),
    )
    assert preview.package_id == ""
    assert preview.result_state == DETACH_BLOCKED_PACKAGE_NOT_VERIFIED
    assert any("pinned package missing" in b for b in preview.blockers)

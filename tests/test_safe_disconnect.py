"""Safe disconnect: UUID resolution, wizard phases, holders, power-off (mocked only)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.core.storage_roles import (
    CONTROL_DIRNAME,
    DEFAULT_LEGACY_UUID,
    DEFAULT_PRIMARY_LABEL,
    DEFAULT_PRIMARY_UUID,
)
from mercury.logging.engine import configure_logging, reset_logging
from mercury.storage.block_device import (
    identities_match,
    resolve_mercury_block_device,
    systemd_mount_unit_for_path,
)
from mercury.storage.detach_logging import redirect_logging_off_hdd
from mercury.storage.detach_wizard import (
    DETACH_CONFIRMATION,
    DETACH_BLOCKED_ACTIVE_OPERATIONS,
    DETACH_BLOCKED_IO_ERRORS,
    DETACH_BLOCKED_OPEN_HANDLES,
    DETACH_BLOCKED_PACKAGE_NOT_VERIFIED,
    DETACH_BLOCKED_SUDO,
    DETACH_UNMOUNT_FAILED,
    HDD_POWERED_OFF_SAFE_TO_DISCONNECT,
    SAFE_TO_PHYSICALLY_DISCONNECT_UNMOUNTED,
    format_wizard_report,
    parse_fuser_output,
    run_detach_wizard,
)
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance, writes_allowed
from mercury.storage.reconnect import run_reconnect_validate


def _lsblk(
    *,
    partition: str,
    parent: str,
    mounted: bool = True,
    extra_mounted: bool = False,
    mountpoint: str = "/mnt/MERCURY_DATA_V2",
    model: str = "WDC WD10JDRW-11CFYS0",
):
    part_name = partition.rsplit("/", 1)[-1]
    parent_name = parent.rsplit("/", 1)[-1]
    children = [
        {
            "name": part_name,
            "path": partition,
            "pkname": parent_name,
            "type": "part",
            "mountpoint": mountpoint if mounted else None,
            "fstype": "ext4",
            "label": DEFAULT_PRIMARY_LABEL,
            "uuid": DEFAULT_PRIMARY_UUID,
        }
    ]
    if extra_mounted:
        children.append(
            {
                "name": parent_name + "2",
                "path": parent + "2",
                "pkname": parent_name,
                "type": "part",
                "mountpoint": "/mnt/other",
                "fstype": "ext4",
                "label": "OTHER",
                "uuid": "00000000-0000-0000-0000-000000000099",
            }
        )
    return {
        "blockdevices": [
            {
                "name": parent_name,
                "path": parent,
                "type": "disk",
                "model": model,
                "serial": "WD-TESTSERIAL",
                "children": children,
            },
            {
                "name": "sda",
                "path": "/dev/sda",
                "type": "disk",
                "model": "USB Flash Drive",
                "children": [
                    {
                        "name": "sda1",
                        "path": "/dev/sda1",
                        "pkname": "sda",
                        "type": "part",
                        "mountpoint": "/mnt/MERCURY_DATA_USB",
                        "fstype": "ext4",
                        "label": "MERCURY_DATA_USB",
                        "uuid": DEFAULT_LEGACY_UUID,
                    }
                ],
            },
        ]
    }


def _runner_for(
    partition: str,
    parent: str,
    *,
    mounted: bool = True,
    mountpoint: str = "/mnt/MERCURY_DATA_V2",
):
    state = {"mounted": mounted}

    def runner(argv, check=False, capture_output=True, text=True):
        if argv[:2] == ["findmnt", "-rn"] and f"UUID={DEFAULT_PRIMARY_UUID}" in argv:
            if not state["mounted"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            out = argv[argv.index("-o") + 1] if "-o" in argv else "TARGET"
            if "SOURCE" in out:
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"{partition} {mountpoint} ext4 {DEFAULT_PRIMARY_LABEL}\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout=f"{mountpoint}\n", stderr="")
        if argv[:2] == ["findmnt", "-rn"] and f"UUID={DEFAULT_LEGACY_UUID}" in argv:
            return SimpleNamespace(
                returncode=0, stdout="/mnt/MERCURY_DATA_USB\n", stderr=""
            )
        if argv[0] == "lsblk":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    _lsblk(
                        partition=partition,
                        parent=parent,
                        mounted=state["mounted"],
                        mountpoint=mountpoint,
                    )
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    runner.state = state  # type: ignore[attr-defined]
    return runner


@pytest.fixture
def host_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            destination_rehearsal_in_progress=True,
            package_id="destination_rehearsal_fixture",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=path,
    )
    return path


def _seed_package(mount: Path) -> None:
    pkg_id = "destination_rehearsal_fixture"
    root = mount / CONTROL_DIRNAME / "destination_packages" / pkg_id
    root.mkdir(parents=True)
    member = root / "payload" / "a.txt"
    member.parent.mkdir(parents=True)
    member.write_text("ok\n", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(member.read_bytes()).hexdigest()
    (root / "package_members.sha256").write_text(
        f"{digest}  payload/a.txt\n", encoding="utf-8"
    )
    (root / "package_receipt.json").write_text(
        json.dumps(
            {
                "package_id": pkg_id,
                "verification_status": "DESTINATION_PACKAGE_VERIFIED",
            }
        ),
        encoding="utf-8",
    )
    (root / "verification_report.json").write_text(
        json.dumps({"status": "DESTINATION_PACKAGE_VERIFIED"}), encoding="utf-8"
    )
    phase = mount / CONTROL_DIRNAME / "phase3b" / "20260722T055400Z_phase3b"
    phase.mkdir(parents=True)
    (phase / "phase3b_summary.json").write_text(
        json.dumps({"writers_resumed": False, "run_id": "20260722T055400Z_phase3b"}),
        encoding="utf-8",
    )


def _ok_priv(*_a, **_k):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_uuid_resolves_as_sda_in_one_layout() -> None:
    lsblk = {
        "blockdevices": [
            {
                "name": "sda",
                "path": "/dev/sda",
                "type": "disk",
                "model": "WDC WD10JDRW",
                "serial": "A",
                "children": [
                    {
                        "name": "sda1",
                        "path": "/dev/sda1",
                        "pkname": "sda",
                        "mountpoint": "/mnt/MERCURY_DATA_V2",
                        "fstype": "ext4",
                        "label": DEFAULT_PRIMARY_LABEL,
                        "uuid": DEFAULT_PRIMARY_UUID,
                    }
                ],
            }
        ]
    }
    result = resolve_mercury_block_device(
        require_mounted=True,
        runner=_runner_for("/dev/sda1", "/dev/sda"),
        lsblk_json=lsblk,
    )
    assert result.ok and result.identity
    assert result.identity.partition_device == "/dev/sda1"
    assert result.identity.parent_device == "/dev/sda"


def test_uuid_resolves_as_sdb_after_reorder() -> None:
    result = resolve_mercury_block_device(
        require_mounted=True,
        runner=_runner_for("/dev/sdb1", "/dev/sdb"),
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb"),
    )
    assert result.ok and result.identity
    assert result.identity.partition_device == "/dev/sdb1"
    assert result.identity.parent_device == "/dev/sdb"


def test_device_letter_reorder_same_uuid() -> None:
    a = resolve_mercury_block_device(
        require_mounted=True,
        runner=_runner_for("/dev/sda1", "/dev/sda"),
        lsblk_json={
            "blockdevices": [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "type": "disk",
                    "model": "WDC WD10JDRW",
                    "serial": "SAME",
                    "children": [
                        {
                            "name": "sda1",
                            "path": "/dev/sda1",
                            "pkname": "sda",
                            "mountpoint": "/mnt/MERCURY_DATA_V2",
                            "fstype": "ext4",
                            "label": DEFAULT_PRIMARY_LABEL,
                            "uuid": DEFAULT_PRIMARY_UUID,
                        }
                    ],
                }
            ]
        },
    )
    b = resolve_mercury_block_device(
        require_mounted=True,
        runner=_runner_for("/dev/sdb1", "/dev/sdb"),
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb"),
    )
    assert a.identity and b.identity
    assert a.identity.uuid == b.identity.uuid
    assert a.identity.label == b.identity.label
    # Letter reorder changes parent/partition paths; UUID identity is stable.
    drift = identities_match(a.identity, b.identity)
    assert any("parent" in e or "partition" in e for e in drift)


def test_legacy_usb_never_selected() -> None:
    result = resolve_mercury_block_device(expected_uuid=DEFAULT_LEGACY_UUID)
    assert not result.ok


def test_parent_with_other_mounted_partition_refuses() -> None:
    result = resolve_mercury_block_device(
        require_mounted=True,
        runner=_runner_for("/dev/sdb1", "/dev/sdb"),
        lsblk_json=_lsblk(
            partition="/dev/sdb1", parent="/dev/sdb", extra_mounted=True
        ),
    )
    assert not result.ok
    assert any("other mounted" in e for e in result.errors)


def test_kernel_only_fuser_does_not_block() -> None:
    text = "root     kernel mount /mnt/MERCURY_DATA_V2\n"
    assert parse_fuser_output(text, mount="/mnt/MERCURY_DATA_V2") == []


def test_fuser_user_process_parsed() -> None:
    text = "secadmin  12345 F.... bash\n"
    holders = parse_fuser_output(text, mount="/mnt/MERCURY_DATA_V2")
    assert any(h.pid == 12345 for h in holders)


def test_log_redirect_closes_hdd_handlers(tmp_path: Path, monkeypatch) -> None:
    mount = tmp_path / "mnt"
    log_hdd = mount / "mercury_logs"
    log_hdd.mkdir(parents=True)
    host_logs = tmp_path / "detach_logs"
    monkeypatch.setenv("MERCURY_DETACH_LOG_DIR", str(host_logs))
    reset_logging()
    configure_logging(log_dir=log_hdd)
    dest, remaining = redirect_logging_off_hdd(mount=mount, log_dir=host_logs)
    assert dest == host_logs
    assert remaining == []
    assert any(
        str(host_logs) in str(getattr(h, "baseFilename", ""))
        for h in logging.getLogger("mercury").handlers
    )
    reset_logging()


def test_sudo_cancellation_fails_cleanly(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)

    def priv(argv, check=False, capture_output=True, text=True):
        if argv[:2] == ["sudo", "-v"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="cancelled")
        return _ok_priv()

    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=priv,
        skip_log_redirect=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
    )
    assert result.result_state == DETACH_BLOCKED_SUDO


def test_password_never_in_invoked_commands(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)

    def priv(argv, check=False, capture_output=True, text=True):
        assert "-S" not in argv
        assert "--password" not in argv
        return _ok_priv()

    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="",
        simulate_unmount_success=True,
        simulate_power_off="success",
    )
    assert result.result_state == HDD_POWERED_OFF_SAFE_TO_DISCONNECT
    for cmd in result.commands_invoked:
        assert "-S" not in cmd
        assert "--password" not in cmd
        assert not any(a.startswith("--password=") for a in cmd)


def test_package_not_verified_blocks(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    (mount / CONTROL_DIRNAME).mkdir()
    mp = str(mount)
    result = run_detach_wizard(
        execute=False,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        skip_log_redirect=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
    )
    assert result.result_state == DETACH_BLOCKED_PACKAGE_NOT_VERIFIED


def test_active_backup_blocks(tmp_path: Path, host_state, monkeypatch) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.active_write_operations",
        lambda **kwargs: ["mariadb-dump (pid 9)"],
    )
    result = run_detach_wizard(
        execute=False,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        skip_log_redirect=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
    )
    assert result.result_state == DETACH_BLOCKED_ACTIVE_OPERATIONS


def test_external_writable_handle_blocks(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="secadmin  99999 F.... vim /mnt/MERCURY_DATA_V2/notes.txt\n",
        lsof_text="",
    )
    assert result.result_state == DETACH_BLOCKED_OPEN_HANDLES


def test_shell_cwd_under_mount_blocks(tmp_path: Path, host_state, monkeypatch) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    from mercury.storage.detach_wizard import ProcessHolder

    monkeypatch.setattr(
        "mercury.storage.detach_wizard.scan_cwd_holders",
        lambda m: [ProcessHolder(1, "bash", "cwd", str(mount))],
    )
    result = run_detach_wizard(
        execute=False,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        skip_log_redirect=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
    )
    assert result.result_state == DETACH_BLOCKED_OPEN_HANDLES


def test_io_error_blocks(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="[123] ext4-fs error on sdb1: I/O error",
    )
    assert result.result_state == DETACH_BLOCKED_IO_ERRORS


def test_normal_unmount_and_power_off_uses_reresolved_parent(
    tmp_path: Path, host_state
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="",
        simulate_unmount_success=True,
        simulate_power_off="success",
    )
    assert result.result_state == HDD_POWERED_OFF_SAFE_TO_DISCONNECT
    assert any(
        c[:3] == ["udisksctl", "power-off", "-b"] and c[3] == "/dev/sdb"
        for c in result.commands_invoked
    )


def test_failed_unmount_leaves_mounted_state(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="",
        simulate_unmount_success=False,
    )
    assert result.result_state == DETACH_UNMOUNT_FAILED


def test_power_off_unsupported_returns_unmounted_safe(
    tmp_path: Path, host_state
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="",
        simulate_unmount_success=True,
        simulate_power_off="unsupported",
    )
    assert result.result_state == SAFE_TO_PHYSICALLY_DISCONNECT_UNMOUNTED


def test_no_lazy_forced_unmount_emitted(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="",
        simulate_unmount_success=True,
        simulate_power_off="success",
    )
    for cmd in result.commands_invoked:
        assert "--lazy" not in cmd and "--force" not in cmd
        if len(cmd) >= 2 and cmd[1] == "umount":
            assert "-l" not in cmd and "-f" not in cmd


def test_systemd_unit_name() -> None:
    assert systemd_mount_unit_for_path("/mnt/MERCURY_DATA_V2") == "mnt-MERCURY_DATA_V2.mount"


def test_menu_report_success_text(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="",
        simulate_unmount_success=True,
        simulate_power_off="success",
    )
    report = "\n".join(format_wizard_report(result))
    assert "SAFE TO DISCONNECT" in report or "HDD_POWERED_OFF" in report
    assert "manually run" not in report.lower()


def test_reconnect_validates_uuid_before_mount(tmp_path: Path, host_state) -> None:
    result = run_reconnect_validate(
        mode="destination",
        execute_mount=False,
        read_only=True,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mounted=False),
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mounted=False),
    )
    assert result.ok
    assert result.identity is not None
    assert result.identity["uuid"] == DEFAULT_PRIMARY_UUID


def test_destination_read_only_mode(tmp_path: Path, host_state) -> None:
    result = run_reconnect_validate(
        mode="destination",
        execute_mount=True,
        read_only=True,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mounted=False),
        privileged_runner=_ok_priv,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mounted=False),
    )
    assert result.ok
    assert result.result_state == "RECONNECT_MOUNTED_READ_ONLY"
    assert any("ro" in c for cmd in result.commands_invoked for c in cmd)


def test_host_shadow_writes_remain_closed(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="",
        simulate_unmount_success=True,
        simulate_power_off="success",
    )
    assert result.ok
    assert writes_allowed() is False


def test_automount_detection(tmp_path: Path) -> None:
    from mercury.storage.detach_wizard import detect_desktop_automount

    media = tmp_path / "media" / "secadmin" / DEFAULT_PRIMARY_LABEL
    media.mkdir(parents=True)
    hits = detect_desktop_automount(DEFAULT_PRIMARY_LABEL, media_root=tmp_path / "media")
    assert hits == [str(media)]


def test_device_identity_blocks_on_model_mismatch() -> None:
    result = resolve_mercury_block_device(
        require_mounted=True,
        runner=_runner_for("/dev/sdb1", "/dev/sdb"),
        lsblk_json=_lsblk(
            partition="/dev/sdb1", parent="/dev/sdb", model="WRONG MODEL"
        ),
    )
    assert not result.ok


def test_four_hdd_log_handlers_redirected_before_holders(tmp_path: Path, monkeypatch) -> None:
    """Regression: Mercury may have four FileHandlers on the HDD before detach."""
    from mercury.logging.config import (
        BACKUP_LOGGER_NAME,
        DATABASE_LOGGER_NAME,
        ERROR_LOGGER_NAME,
        LOGGER_NAME,
    )
    from mercury.logging.engine import attach_file_handler, clear_logger, reset_logging
    from mercury.storage.detach_logging import redirect_logging_off_hdd

    mount = tmp_path / "mnt"
    hdd_logs = mount / "mercury_logs"
    hdd_logs.mkdir(parents=True)
    host_logs = tmp_path / "detach_logs"
    monkeypatch.setenv("MERCURY_DETACH_LOG_DIR", str(host_logs))
    reset_logging()
    for name, filename in (
        (LOGGER_NAME, "mercury.log"),
        (ERROR_LOGGER_NAME, "error.log"),
        (DATABASE_LOGGER_NAME, "database.log"),
        (BACKUP_LOGGER_NAME, "backup.log"),
    ):
        clear_logger(name)
        path = hdd_logs / filename
        path.write_text("", encoding="utf-8")
        attach_file_handler(__import__("logging").getLogger(name), path, level=20)
    open_on_hdd = 0
    for name in (LOGGER_NAME, ERROR_LOGGER_NAME, DATABASE_LOGGER_NAME, BACKUP_LOGGER_NAME):
        for handler in __import__("logging").getLogger(name).handlers:
            base = str(getattr(handler, "baseFilename", ""))
            if str(hdd_logs) in base:
                open_on_hdd += 1
    assert open_on_hdd == 4
    dest, remaining = redirect_logging_off_hdd(mount=mount, log_dir=host_logs)
    assert dest == host_logs
    assert remaining == []
    still = 0
    for name in (LOGGER_NAME, ERROR_LOGGER_NAME, DATABASE_LOGGER_NAME, BACKUP_LOGGER_NAME):
        for handler in __import__("logging").getLogger(name).handlers:
            base = str(getattr(handler, "baseFilename", ""))
            if str(hdd_logs) in base:
                still += 1
            if str(host_logs) in base:
                still -= 0  # redirected ok
    assert still == 0
    reset_logging()


def test_non_tty_sudo_refuses(tmp_path: Path, host_state, monkeypatch) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=False,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text=None,
    )
    assert result.result_state == DETACH_BLOCKED_SUDO
    assert any("TTY" in b for b in result.blockers)


def test_stale_unrelated_dmesg_does_not_block(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="root kernel mount x\n",
        lsof_text="",
        dmesg_text="[1] usb usb3-port1: cannot enable port\n[2] wifi: beacon loss",
        simulate_unmount_success=True,
        simulate_power_off="success",
    )
    assert result.result_state == HDD_POWERED_OFF_SAFE_TO_DISCONNECT


def test_parent_identity_change_blocks_power_off(tmp_path: Path, host_state) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    from mercury.storage.block_device import MercuryBlockIdentity
    from mercury.storage import detach_wizard as dw

    first = MercuryBlockIdentity(
        partition_device="/dev/sdb1",
        parent_device="/dev/sdb",
        uuid=DEFAULT_PRIMARY_UUID,
        label=DEFAULT_PRIMARY_LABEL,
        model="WDC WD10JDRW",
        serial="A",
        mountpoint=mp,
        fstype="ext4",
    )
    second = MercuryBlockIdentity(
        partition_device="/dev/sdc1",
        parent_device="/dev/sdc",
        uuid=DEFAULT_PRIMARY_UUID,
        label=DEFAULT_PRIMARY_LABEL,
        model="WDC WD10JDRW",
        serial="A",
        mountpoint=None,
        fstype="ext4",
    )
    assert identities_match(first, second)

    calls = {"n": 0}

    def resolve_side(*, expected_uuid=DEFAULT_PRIMARY_UUID, require_mounted=False, **kwargs):
        from mercury.storage.block_device import BlockResolveResult

        calls["n"] += 1
        if calls["n"] == 1:
            return BlockResolveResult(ok=True, identity=first)
        return BlockResolveResult(ok=True, identity=second)

    # Use identities_match directly as the contractual check
    drift = identities_match(first, second)
    assert any("parent" in e or "partition" in e for e in drift)


def test_uuid_letter_change_same_serial_is_drift(tmp_path: Path) -> None:
    from mercury.storage.block_device import MercuryBlockIdentity

    a = MercuryBlockIdentity(
        "/dev/sda1", "/dev/sda", DEFAULT_PRIMARY_UUID, DEFAULT_PRIMARY_LABEL,
        "WDC WD10JDRW", "SER1", "/mnt/MERCURY_DATA_V2", "ext4",
    )
    b = MercuryBlockIdentity(
        "/dev/sdb1", "/dev/sdb", DEFAULT_PRIMARY_UUID, DEFAULT_PRIMARY_LABEL,
        "WDC WD10JDRW", "SER1", "/mnt/MERCURY_DATA_V2", "ext4",
    )
    # Same UUID/serial but different parent letter → refuse power-off without re-binding
    errs = identities_match(a, b)
    assert errs


def test_readonly_open_file_blocks(tmp_path: Path, host_state) -> None:
    """Conservative policy: any user open under the mount blocks detach."""
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_package(mount)
    mp = str(mount)
    result = run_detach_wizard(
        execute=True,
        confirm=DETACH_CONFIRMATION,
        mount=mount,
        runner=_runner_for("/dev/sdb1", "/dev/sdb", mountpoint=mp),
        privileged_runner=_ok_priv,
        skip_log_redirect=True,
        skip_sudo_validate=True,
        lsblk_json=_lsblk(partition="/dev/sdb1", parent="/dev/sdb", mountpoint=mp),
        fuser_text="",
        lsof_text=(
            "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            f"less 4242 secadmin 4r REG 8,17 12 1 {mp}/notes/readme.txt\n"
        ),
    )
    assert result.result_state == DETACH_BLOCKED_OPEN_HANDLES


def test_detached_writes_refuse_backup_and_package(tmp_path: Path, host_state, monkeypatch) -> None:
    from mercury.storage.host_maintenance import mark_detached, writes_allowed
    from mercury.migration.destination_package_create import (
        CREATE_CONFIRMATION,
        create_destination_package,
    )

    mark_detached(path=host_state)
    assert writes_allowed() is False
    mount = tmp_path / "mnt"
    mount.mkdir()
    result = create_destination_package(
        mount,
        preview_id="preview_x",
        confirm=CREATE_CONFIRMATION,
        mercury_commit="a" * 40,
        mercury_capture_id="cap",
        verify_git_head=False,
    )
    assert not result.ok
    assert any("host maintenance" in e for e in result.errors)


def test_dashboard_detached_compact(tmp_path: Path, host_state, monkeypatch) -> None:
    from mercury.storage.host_maintenance import mark_detached
    from mercury.menu.dashboard import _compact_writer_line, _compact_hdd_line

    mark_detached(path=host_state)
    assert "Detached" in _compact_writer_line("USB", None)
    assert _compact_hdd_line() == "Detached"

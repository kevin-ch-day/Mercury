"""Tests for Mercury USB repair command helpers."""

from mercury.repair.usb import USB_REPAIR_COMMAND, describe_usb_repair, usb_repair_script_path


def test_usb_repair_command_is_run_sh_entrypoint() -> None:
    assert USB_REPAIR_COMMAND == "./run.sh repair-usb"


def test_usb_repair_script_exists() -> None:
    assert usb_repair_script_path().name == "repair-mercury-usb.sh"


def test_describe_usb_repair_mentions_mount_and_ownership() -> None:
    text = "\n".join(describe_usb_repair())
    assert "mount" in text.lower()
    assert "root-owned" in text.lower() or "ownership" in text.lower()
    assert USB_REPAIR_COMMAND in text
    assert "MERCURY_DATA_V2" in text or "primary" in text.lower()
    assert "storage status" in text.lower()

"""Resolve Mercury HDD block devices by filesystem UUID (never by fixed /dev letter)."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from mercury.core.storage_roles import (
    DEFAULT_FILESYSTEM_TYPE,
    DEFAULT_LEGACY_LABEL,
    DEFAULT_LEGACY_UUID,
    DEFAULT_PRIMARY_LABEL,
    DEFAULT_PRIMARY_MOUNT,
    DEFAULT_PRIMARY_UUID,
)

EXPECTED_PRIMARY_MODEL = "WDC WD10JDRW"

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class MercuryBlockIdentity:
    partition_device: str
    parent_device: str
    uuid: str
    label: str
    model: str
    serial: str
    mountpoint: str | None
    fstype: str
    other_mounted_partitions_on_parent: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "partition_device": self.partition_device,
            "parent_device": self.parent_device,
            "uuid": self.uuid,
            "label": self.label,
            "model": self.model,
            "serial": self.serial,
            "mountpoint": self.mountpoint,
            "fstype": self.fstype,
            "other_mounted_partitions_on_parent": list(
                self.other_mounted_partitions_on_parent
            ),
        }


@dataclass
class BlockResolveResult:
    ok: bool
    identity: MercuryBlockIdentity | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def result_state(self) -> str:
        return "OK" if self.ok else "DETACH_BLOCKED_DEVICE_IDENTITY"


def _default_runner(
    argv: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=check,
        capture_output=capture_output,
        text=text,
    )


def _parent_from_partition(partition: str) -> str:
    """Best-effort parent path when lsblk PKNAME is missing (tests / odd layouts)."""
    name = Path(partition).name
    # nvme0n1p1 -> nvme0n1; sdb1 -> sdb; mmcblk0p1 -> mmcblk0
    if re.match(r"^nvme\d+n\d+p\d+$", name):
        return f"/dev/{re.sub(r'p\d+$', '', name)}"
    if re.match(r"^mmcblk\d+p\d+$", name):
        return f"/dev/{re.sub(r'p\d+$', '', name)}"
    stripped = re.sub(r"\d+$", "", name)
    if stripped and stripped != name:
        return f"/dev/{stripped}"
    return partition


def systemd_mount_unit_for_path(mount_path: str | Path) -> str:
    """Derive systemd .mount unit name from absolute mount path."""
    path = str(mount_path).rstrip("/") or "/"
    escaped = path.lstrip("/").replace("-", "\\x2d").replace("/", "-")
    return f"{escaped}.mount"


def find_mountpoints_for_uuid(
    filesystem_uuid: str,
    *,
    runner: Runner | None = None,
) -> list[str]:
    run = runner or _default_runner
    completed = run(
        ["findmnt", "-rn", "-S", f"UUID={filesystem_uuid}", "-o", "TARGET"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in (0, 1):
        return []
    return [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]


def resolve_mercury_block_device(
    *,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    expected_label: str = DEFAULT_PRIMARY_LABEL,
    expected_mount: str = DEFAULT_PRIMARY_MOUNT,
    expected_fstype: str = DEFAULT_FILESYSTEM_TYPE,
    expected_model: str = EXPECTED_PRIMARY_MODEL,
    require_mounted: bool = False,
    refuse_legacy_uuid: str = DEFAULT_LEGACY_UUID,
    runner: Runner | None = None,
    lsblk_json: dict[str, Any] | None = None,
) -> BlockResolveResult:
    """Resolve UUID → partition → parent. Never trusts a fixed device letter."""
    errors: list[str] = []
    if not expected_uuid or expected_uuid == refuse_legacy_uuid:
        return BlockResolveResult(
            ok=False, errors=["UUID missing or matches legacy MERCURY_DATA_USB"]
        )
    if expected_uuid == DEFAULT_LEGACY_UUID:
        return BlockResolveResult(
            ok=False, errors=["refusing to select legacy MERCURY_DATA_USB UUID"]
        )

    run = runner or _default_runner

    # Discover devices that carry this UUID via findmnt and/or lsblk JSON.
    findmnt = run(
        [
            "findmnt",
            "-rn",
            "-S",
            f"UUID={expected_uuid}",
            "-o",
            "SOURCE,TARGET,FSTYPE,LABEL",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    findmnt_rows: list[tuple[str, str, str, str]] = []
    for line in (findmnt.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            source = parts[0]
            target = parts[1]
            fstype = parts[2] if len(parts) > 2 else ""
            label = parts[3] if len(parts) > 3 else ""
            findmnt_rows.append((source, target, fstype, label))

    if len(findmnt_rows) > 1:
        return BlockResolveResult(
            ok=False,
            errors=[
                f"more than one mount reports UUID {expected_uuid}: "
                f"{[r[0] for r in findmnt_rows]}"
            ],
        )

    if lsblk_json is None:
        completed = run(
            [
                "lsblk",
                "--json",
                "-b",
                "-o",
                "NAME,PATH,PKNAME,TYPE,MODEL,SERIAL,MOUNTPOINT,FSTYPE,LABEL,UUID",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return BlockResolveResult(
                ok=False,
                errors=[f"lsblk --json failed: {(completed.stderr or '').strip()}"],
            )
        try:
            lsblk_json = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            return BlockResolveResult(ok=False, errors=[f"lsblk JSON parse failed: {exc}"])

    devices = list(lsblk_json.get("blockdevices") or [])
    # Flatten children
    flat: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]], parent_name: str | None = None) -> None:
        for node in nodes:
            entry = dict(node)
            if parent_name and not entry.get("pkname"):
                entry["pkname"] = parent_name
            flat.append(entry)
            kids = entry.get("children") or []
            if kids:
                walk(kids, parent_name=str(entry.get("name") or ""))

    walk(devices)

    uuid_matches = [
        d
        for d in flat
        if str(d.get("uuid") or "") == expected_uuid
        or (
            findmnt_rows
            and str(d.get("path") or f"/dev/{d.get('name')}") == findmnt_rows[0][0]
        )
    ]
    # Prefer exact UUID match
    exact = [d for d in flat if str(d.get("uuid") or "") == expected_uuid]
    if len(exact) > 1:
        return BlockResolveResult(
            ok=False,
            errors=[f"more than one block device reports UUID {expected_uuid}"],
        )
    if exact:
        uuid_matches = exact
    elif findmnt_rows:
        source = findmnt_rows[0][0]
        uuid_matches = [
            d
            for d in flat
            if str(d.get("path") or f"/dev/{d.get('name')}") == source
            or f"/dev/{d.get('name')}" == source
        ]
    else:
        uuid_matches = []

    if not uuid_matches:
        if require_mounted:
            return BlockResolveResult(
                ok=False,
                errors=[f"UUID {expected_uuid} not found among block devices / mounts"],
            )
        return BlockResolveResult(
            ok=False,
            errors=[f"UUID {expected_uuid} absent (disk may already be detached)"],
        )

    part = uuid_matches[0]
    partition = str(part.get("path") or f"/dev/{part.get('name')}")
    label = str(part.get("label") or (findmnt_rows[0][3] if findmnt_rows else "") or "")
    fstype = str(part.get("fstype") or (findmnt_rows[0][2] if findmnt_rows else "") or "")
    mountpoint = part.get("mountpoint")
    if mountpoint in ("", None):
        mountpoint = findmnt_rows[0][1] if findmnt_rows else None
    else:
        mountpoint = str(mountpoint)

    pkname = str(part.get("pkname") or "").strip()
    if pkname:
        parent = f"/dev/{pkname}" if not pkname.startswith("/dev/") else pkname
    else:
        parent = _parent_from_partition(partition)

    # Model/serial live on the parent disk node in lsblk.
    parent_nodes = [
        d
        for d in flat
        if str(d.get("path") or f"/dev/{d.get('name')}") == parent
        or f"/dev/{d.get('name')}" == parent
    ]
    model = ""
    serial = ""
    if parent_nodes:
        model = str(parent_nodes[0].get("model") or "").strip()
        serial = str(parent_nodes[0].get("serial") or "").strip()

    other_mounted: list[str] = []
    for d in flat:
        d_path = str(d.get("path") or f"/dev/{d.get('name')}")
        d_pk = str(d.get("pkname") or "")
        d_parent = (
            f"/dev/{d_pk}"
            if d_pk and not d_pk.startswith("/dev/")
            else (d_pk or _parent_from_partition(d_path))
        )
        if d_parent != parent:
            continue
        if d_path == partition:
            continue
        mp = d.get("mountpoint")
        if mp:
            other_mounted.append(f"{d_path}:{mp}")

    if label and label == DEFAULT_LEGACY_LABEL:
        errors.append("resolved label is MERCURY_DATA_USB (legacy archive)")
    if str(part.get("uuid") or "") == refuse_legacy_uuid:
        errors.append("resolved UUID is legacy MERCURY_DATA_USB")
    if label and label != expected_label:
        errors.append(f"label mismatch: got {label!r} expected {expected_label!r}")
    if fstype and fstype != expected_fstype:
        errors.append(f"fstype mismatch: got {fstype!r} expected {expected_fstype!r}")
    if require_mounted:
        if not mountpoint:
            errors.append(f"UUID {expected_uuid} is not mounted (required)")
        elif mountpoint != expected_mount:
            errors.append(
                f"mountpoint mismatch: got {mountpoint!r} expected {expected_mount!r}"
            )
    elif mountpoint and mountpoint != expected_mount:
        errors.append(
            f"mountpoint mismatch: got {mountpoint!r} expected {expected_mount!r}"
        )
    if model and expected_model and expected_model not in model and model not in expected_model:
        errors.append(f"model mismatch: got {model!r} expected to contain {expected_model!r}")
    if other_mounted:
        errors.append(
            "parent device has other mounted partitions: " + ", ".join(other_mounted)
        )

    identity = MercuryBlockIdentity(
        partition_device=partition,
        parent_device=parent,
        uuid=expected_uuid,
        label=label or expected_label,
        model=model,
        serial=serial,
        mountpoint=mountpoint,
        fstype=fstype or expected_fstype,
        other_mounted_partitions_on_parent=tuple(other_mounted),
    )
    return BlockResolveResult(ok=not errors, identity=identity, errors=errors)


def identities_match(
    a: MercuryBlockIdentity, b: MercuryBlockIdentity, *, require_same_parent: bool = True
) -> list[str]:
    """Compare two resolutions; used before power-off."""
    errs: list[str] = []
    if a.uuid != b.uuid:
        errs.append("UUID changed during wizard")
    if a.partition_device != b.partition_device:
        errs.append(
            f"partition device changed: {a.partition_device} → {b.partition_device}"
        )
    if require_same_parent and a.parent_device != b.parent_device:
        errs.append(f"parent device changed: {a.parent_device} → {b.parent_device}")
    if a.label and b.label and a.label != b.label:
        errs.append("label changed during wizard")
    if a.model and b.model and a.model != b.model:
        errs.append("model changed during wizard")
    if a.serial and b.serial and a.serial != b.serial:
        errs.append("serial changed during wizard")
    return errs

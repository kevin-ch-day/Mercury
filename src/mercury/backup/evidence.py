"""Explicit bounded research-file preservation; never discovers or copies APKs."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path

from mercury.backup.durability import durable_replace_text, sync_directory, sync_tree
from mercury.repo.snapshot import sensitive


def _relative(name: str) -> Path:
    p = Path(name)
    if not p.parts or p.is_absolute() or ".." in p.parts or ".git" in p.parts:
        raise ValueError("Unsafe evidence member name")
    return p


def preserve_files(plan: dict, destination: Path, *, guard) -> dict:
    """Publish a new private generation from an explicit hash-bound selection."""
    budget = plan["max_bytes"]
    if type(budget) is not int or not 0 < budget <= 512 * 1024**2:
        raise ValueError("Evidence plan requires a budget of at most 512 MiB")
    names = [x["name"] for x in plan["files"]]
    if not names or len(names) > 10000 or len(set(names)) != len(names):
        raise ValueError("Invalid evidence selection inventory")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Evidence destination already exists")
    guard(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = destination.with_name(destination.name + ".incomplete")
    staging.mkdir(mode=0o700)
    durable_replace_text(
        staging / "STATE.json", json.dumps({"status": "incomplete"}) + "\n"
    )
    inventory = []
    total = 0
    with tarfile.open(staging / "files.tar.gz", "w:gz") as archive:
        for row in plan["files"]:
            rel = _relative(row["name"])
            source = Path(row["source"])
            if source.suffix.lower() in {".apk", ".aab", ".apks", ".xapk"} or sensitive(
                str(rel)
            ):
                raise ValueError(
                    "APK or sensitive filename is outside metadata/evidence scope"
                )
            if not source.is_absolute() or source.resolve() != source:
                raise ValueError("Evidence source requires an absolute nonsymlink path")
            guard(staging)
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > budget - total:
                    raise ValueError("Evidence source type or byte budget refused")
                data = handle.read(budget - total + 1)
                after = os.fstat(handle.fileno())
            if (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Evidence source changed while reading")
            digest = hashlib.sha256(data).hexdigest()
            if digest != row["sha256"] or sensitive("", data):
                raise ValueError("Evidence pin or secret-content admission refused")
            total += len(data)
            if total > budget:
                raise ValueError("Evidence byte budget exceeded")
            info = tarfile.TarInfo(str(rel))
            info.size = len(data)
            info.mode = stat.S_IMODE(before.st_mode) & 0o777
            archive.addfile(info, io.BytesIO(data))
            inventory.append(
                {
                    "name": str(rel),
                    "source": str(source),
                    "sha256": digest,
                    "bytes": len(data),
                    "mode": info.mode,
                }
            )
    (staging / "files.tar.gz").chmod(0o600)
    manifest = {
        "status": "complete_for_selection",
        "files": inventory,
        "source_bytes": total,
        "archive_sha256": hashlib.sha256(
            (staging / "files.tar.gz").read_bytes()
        ).hexdigest(),
        "scope": plan["scope"],
        "excluded_categories": plan.get("excluded_categories", []),
    }
    durable_replace_text(
        staging / "manifest.json", json.dumps(manifest, indent=2) + "\n"
    )
    durable_replace_text(
        staging / "STATE.json", json.dumps({"status": "complete_for_selection"}) + "\n"
    )
    sync_tree(staging)
    guard(destination)
    staging.rename(destination)
    sync_directory(destination.parent)
    return {
        "directory": str(destination),
        "manifest_sha256": hashlib.sha256(
            (destination / "manifest.json").read_bytes()
        ).hexdigest(),
        "files": len(inventory),
        "source_bytes": total,
        "archive_bytes": (destination / "files.tar.gz").stat().st_size,
    }


def restore_files(generation: Path, destination: Path, *, manifest_sha256: str) -> dict:
    if (
        destination.exists()
        or destination.is_symlink()
        or destination.parent.resolve() != destination.parent
    ):
        raise ValueError("Evidence recovery requires a new isolated destination")
    raw = (generation / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise ValueError("Evidence manifest pin mismatch")
    manifest = json.loads(raw)
    if (
        hashlib.sha256((generation / "files.tar.gz").read_bytes()).hexdigest()
        != manifest["archive_sha256"]
    ):
        raise ValueError("Evidence archive digest mismatch")
    entries = {x["name"]: x for x in manifest["files"]}
    destination.mkdir(mode=0o700)
    with tarfile.open(generation / "files.tar.gz", "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(entries) or {x.name for x in members} != set(entries):
            raise ValueError("Evidence restore inventory mismatch")
        for member in members:
            rel = _relative(member.name)
            expected = entries[member.name]
            if not member.isfile() or member.size != expected["bytes"]:
                raise ValueError("Evidence restore member type/size mismatch")
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("Evidence restore content mismatch")
            p = destination / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("xb") as handle:
                handle.write(data)
            p.chmod(expected["mode"] & 0o777)
    return {
        "status": "restored_verified",
        "files": len(entries),
        "destination": str(destination),
        "scope": "Exact selected file bytes and modes; not full collection or scientific reproduction",
    }


def main():
    import argparse

    from mercury.core.storage_roots import load_storage_config
    from mercury.storage.verified_volume import (
        activate_disk,
        exclusive_lock,
        require_destination,
        require_headroom,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--plan", type=Path, required=True)
    backup.add_argument("--destination", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--generation", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if args.command == "restore":
        result = restore_files(
            args.generation, args.destination, manifest_sha256=args.manifest_sha256
        )
    else:
        root = load_storage_config().active_write_root
        activate_disk(str(root.mount_path), root.filesystem_uuid)
        from mercury.backup.write_preflight import assess_backup_write_preflight

        if not assess_backup_write_preflight().allowed:
            raise ValueError("Mercury writes are disabled by host maintenance")
        plan = json.loads(args.plan.read_text())
        require_headroom(root.mount_path, plan["max_bytes"])
        with exclusive_lock(root.mount_path / ".mercury-evidence-backup.lock"):
            result = preserve_files(
                plan,
                args.destination,
                guard=lambda p: require_destination(
                    str(root.mount_path), root.filesystem_uuid, p
                ),
            )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

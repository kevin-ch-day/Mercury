"""Explicit isolated recovery of a Git mirror and a pinned worktree snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

from mercury.repo.snapshot import git_bytes


def recover_repository(
    mirror: Path,
    destination: Path,
    *,
    commit: str,
    snapshot: Path | None = None,
    manifest_sha256: str | None = None,
) -> dict:
    """Never apply over an existing destination. Excluded source remains excluded."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("Recovery requires a new isolated destination")
    if (
        destination.parent.is_symlink()
        or destination.parent.resolve() != destination.parent
    ):
        raise ValueError("Recovery parent must not contain symlinks")
    record = None
    if snapshot is not None:
        raw = (snapshot / "snapshot.json").read_bytes()
        if not manifest_sha256 or hashlib.sha256(raw).hexdigest() != manifest_sha256:
            raise ValueError("Snapshot manifest pin mismatch")
        record = json.loads(raw)
        if (
            record.get("base_commit") != commit
            or record.get("format") != "mercury-worktree-snapshot-v2"
        ):
            raise ValueError("Snapshot commit/format mismatch")
        for name, digest in record["sha256"].items():
            if name not in {"tracked.patch", "untracked.tar.gz"}:
                raise ValueError("Unexpected snapshot artifact")
            if hashlib.sha256((snapshot / name).read_bytes()).hexdigest() != digest:
                raise ValueError("Snapshot artifact digest mismatch")
    git_bytes(mirror, "fsck", "--no-dangling")
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            str(mirror),
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    git_bytes(
        destination, "-c", "core.hooksPath=/dev/null", "checkout", "--detach", commit
    )
    if record is not None:
        git_bytes(destination, "apply", "--check", str(snapshot / "tracked.patch")) if (
            snapshot / "tracked.patch"
        ).stat().st_size else None
        if (snapshot / "tracked.patch").stat().st_size:
            git_bytes(destination, "apply", str(snapshot / "tracked.patch"))
        with tarfile.open(snapshot / "untracked.tar.gz", "r:gz") as archive:
            members = archive.getmembers()
            if {m.name for m in members} != set(record["untracked_files"]) or len(
                members
            ) != len(record["untracked_files"]):
                raise ValueError("Snapshot archive inventory mismatch")
            for m in members:
                rel = Path(m.name)
                if rel.is_absolute() or ".." in rel.parts or ".git" in rel.parts:
                    raise ValueError("Unsafe snapshot path")
                target = destination / rel
                if (
                    not target.parent.resolve().is_relative_to(destination)
                    or target.exists()
                    or target.is_symlink()
                ):
                    raise ValueError("Recovery archive path escape or conflict")
                target.parent.mkdir(parents=True, exist_ok=True)
                if m.issym():
                    link = Path(m.linkname)
                    if link.is_absolute() or not (
                        target.parent / link
                    ).resolve().is_relative_to(destination):
                        raise ValueError("Unsafe snapshot symlink")
                    target.symlink_to(m.linkname)
                elif m.isfile() and m.size <= 8 * 1024 * 1024:
                    data = archive.extractfile(m).read()
                    with target.open("xb") as stream:
                        stream.write(data)
                    target.chmod(m.mode & 0o777)
                else:
                    raise ValueError("Unsupported snapshot member")
        for name, expected in record["files"].items():
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or ".git" in rel.parts:
                raise ValueError("Unsafe snapshot manifest path")
            p = destination / rel
            if not p.parent.resolve().is_relative_to(destination):
                raise ValueError("Snapshot manifest parent escape")
            if expected["kind"] == "deleted":
                if p.exists() or p.is_symlink():
                    raise ValueError("Deleted file unexpectedly restored")
                continue
            if expected["kind"] == "symlink":
                if not p.is_symlink():
                    raise ValueError("Restored symlink missing")
                data = os.fsencode(os.readlink(p))
            else:
                if p.is_symlink() or not p.is_file():
                    raise ValueError("Restored regular file missing")
                data = p.read_bytes()
                p.chmod(expected["mode"] & 0o777)
            if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("Restored file digest mismatch")
    git_bytes(destination, "fsck", "--no-dangling")
    return {
        "status": "restored_verified",
        "commit": commit,
        "destination": str(destination),
        "snapshot_files_verified": len(record["files"]) if record else 0,
        "excluded_files": record["excluded_files"] if record else [],
        "scope": "Git history and selected worktree content; no application execution",
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.receipt.read_text())
    entry = next(x for x in payload["repositories"] if x["key"] == args.key)
    if not entry["synced"] or entry["fsck"] != "PASS":
        raise ValueError("Receipt does not qualify this repository")
    snapshot = entry.get("snapshot") if entry["source_dirty"] else None
    result = recover_repository(
        Path(entry["mirror_path"]),
        args.destination,
        commit=entry["commit"],
        snapshot=Path(snapshot["path"]) if snapshot else None,
        manifest_sha256=snapshot["manifest_sha256"] if snapshot else None,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

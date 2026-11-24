"""SHA-256 helpers for backup artifact verification."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def safe_artifact_name(name: str) -> bool:
    """Backup sidecars name direct children, never external or nested paths."""
    return bool(name) and name not in {".", ".."} and not any(
        character in name for character in "/\\:"
    ) and not any(ord(character) < 32 or ord(character) == 127 for character in name)


def sha256_file(path: Path) -> str:
    """Compute lowercase hex SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_checksum_line(path: Path, hexdigest: str | None = None) -> str:
    """Single checksum line in GNU coreutils style."""
    digest = hexdigest or sha256_file(path)
    return f"{digest}  {path.name}\n"


def render_checksum_text(directory: Path, filenames: list[str]) -> str:
    """Render checksum.sha256 contents for the given filenames in directory."""
    lines: list[str] = []
    for name in filenames:
        artifact = directory / name
        if not artifact.exists():
            raise FileNotFoundError(f"Cannot checksum missing artifact: {artifact}")
        lines.append(format_checksum_line(artifact))
    return "".join(lines)


def write_checksum_file(directory: Path, filenames: list[str], *, output_path: Path | None = None) -> Path:
    """Write checksum.sha256 for the given filenames in directory."""
    checksum_path = output_path or (directory / "checksum.sha256")
    checksum_path.write_text(render_checksum_text(directory, filenames), encoding="utf-8")
    return checksum_path


def parse_checksum_file(path: Path) -> dict[str, str]:
    """Parse checksum.sha256 into {filename: hexdigest}."""
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("checksum sidecar must be a bounded regular file")
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if match is None:
            raise ValueError(f"Malformed checksum entry at line {number}")
        hexdigest, filename = match.groups()
        if not safe_artifact_name(filename) or filename in entries:
            raise ValueError(f"Unsafe or duplicate checksum artifact at line {number}")
        entries[filename] = hexdigest.lower()
    return entries


def verify_checksums(
    directory: Path,
    checksum_path: Path,
    *,
    required_files: dict[str, tuple[str | None, int | None]] | None = None,
) -> tuple[bool, list[str]]:
    """
    Verify checksum.sha256 against files in directory.

    Returns (all_ok, issues).
    """
    issues: list[str] = []
    if not checksum_path.exists():
        return False, ["checksum.sha256 not found"]

    try:
        expected = parse_checksum_file(checksum_path)
    except (OSError, ValueError) as error:
        return False, [f"Invalid checksum sidecar: {type(error).__name__}: {error}"]
    if not expected:
        return False, ["checksum.sha256 is empty or unreadable"]

    all_ok = True
    for filename, (digest, _size) in (required_files or {}).items():
        if filename not in expected:
            all_ok = False
            issues.append(f"Required artifact missing from checksum: {filename}")
        if digest and (
            re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
            or expected.get(filename) != digest.lower()
        ):
            all_ok = False
            issues.append(f"Manifest/checksum digest disagreement for {filename}")
    for filename, recorded in expected.items():
        artifact = directory / filename
        if artifact.is_symlink() or not artifact.is_file():
            all_ok = False
            issues.append(f"Missing artifact referenced in checksum: {filename}")
            continue
        try:
            before = artifact.stat()
            size = (required_files or {}).get(filename, (None, None))[1]
            if before.st_size == 0 or (size is not None and before.st_size != size):
                all_ok = False
                issues.append(f"Artifact size is empty or disagrees with manifest: {filename}")
                continue
            actual = sha256_file(artifact)
            after = artifact.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                all_ok = False
                issues.append(f"Artifact changed during verification: {filename}")
        except OSError:
            all_ok = False
            issues.append(f"Artifact unavailable during verification: {filename}")
            continue
        if actual != recorded:
            all_ok = False
            issues.append(f"Checksum mismatch for {filename}")
    return all_ok, issues

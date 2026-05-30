"""SHA-256 helpers for backup artifact verification."""

from __future__ import annotations

import hashlib
from pathlib import Path


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


def write_checksum_file(directory: Path, filenames: list[str]) -> Path:
    """Write checksum.sha256 for the given filenames in directory."""
    lines: list[str] = []
    for name in filenames:
        artifact = directory / name
        if not artifact.exists():
            raise FileNotFoundError(f"Cannot checksum missing artifact: {artifact}")
        lines.append(format_checksum_line(artifact))
    checksum_path = directory / "checksum.sha256"
    checksum_path.write_text("".join(lines), encoding="utf-8")
    return checksum_path


def parse_checksum_file(path: Path) -> dict[str, str]:
    """Parse checksum.sha256 into {filename: hexdigest}."""
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        hexdigest, filename = parts[0], parts[-1]
        entries[filename] = hexdigest.lower()
    return entries


def verify_checksums(directory: Path, checksum_path: Path) -> tuple[bool, list[str]]:
    """
    Verify checksum.sha256 against files in directory.

    Returns (all_ok, issues).
    """
    issues: list[str] = []
    if not checksum_path.exists():
        return False, ["checksum.sha256 not found"]

    expected = parse_checksum_file(checksum_path)
    if not expected:
        return False, ["checksum.sha256 is empty or unreadable"]

    all_ok = True
    for filename, recorded in expected.items():
        artifact = directory / filename
        if not artifact.exists():
            all_ok = False
            issues.append(f"Missing artifact referenced in checksum: {filename}")
            continue
        if artifact.stat().st_size == 0:
            all_ok = False
            issues.append(f"Artifact is empty: {filename}")
            continue
        actual = sha256_file(artifact)
        if actual != recorded:
            all_ok = False
            issues.append(f"Checksum mismatch for {filename}")
    return all_ok, issues

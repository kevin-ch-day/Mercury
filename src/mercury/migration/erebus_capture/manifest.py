"""Checksum manifest generation with explicit self-referential exclusions."""

from __future__ import annotations

import hashlib
from pathlib import Path


EXCLUDED = frozenset({"checksums.sha256", "checksums.sha256.verify", "manifest_receipt.json"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(root: Path) -> Path:
    """Hash every regular capture file except manifest/verification receipts."""
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in EXCLUDED:
            rows.append(f"{sha256_file(path)}  {path.relative_to(root)}")
    manifest = root / "checksums.sha256"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(root: Path) -> bool:
    manifest = root / "checksums.sha256"
    if not manifest.is_file():
        return False
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        path = root / relative
        if not path.is_file() or sha256_file(path) != digest:
            return False
    return True

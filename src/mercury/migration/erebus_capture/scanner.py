"""Bounded prohibited-content scan for a prepared capture directory."""

from __future__ import annotations

import re
from pathlib import Path

from .contract import validate_members

_PATH_BLOCKED = frozenset({".venv", ".venv-offline", "__pycache__", "logs", "output", "reports", "ScytaleDroid", "ObsidianDroid"})
_SUFFIX_BLOCKED = frozenset({".pyc", ".sqlite", ".db", ".sql", ".dump", ".pem", ".key"})
_CONTENT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|password|credential|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+-]{12,}"),
)


def scan_capture(root: Path, *, short_sha: str, enforce_contract: bool = True) -> list[str]:
    """Return exact refusal findings; inspect only small text-like members."""
    members: set[str] = set()
    findings: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.is_absolute() or ".." in relative.parts:
            findings.append(f"traversal: {relative}"); continue
        if path.is_dir():
            if any(part in _PATH_BLOCKED for part in relative.parts): findings.append(f"forbidden path: {relative}")
            continue
        if path.is_symlink() or not path.is_file():
            findings.append(f"nonregular member: {relative}"); continue
        name = path.name
        members.add(str(relative))
        if any(part in _PATH_BLOCKED for part in relative.parts) or (name == ".env" and path.stat().st_size > 0) or path.suffix.lower() in _SUFFIX_BLOCKED:
            findings.append(f"forbidden path: {relative}"); continue
        if path.stat().st_size <= 1024 * 1024:
            try: text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError: continue
            if name != ".env.example" and any(pattern.search(text) for pattern in _CONTENT_PATTERNS):
                findings.append(f"sensitive content: {relative}")
    if enforce_contract:
        findings.extend(validate_members(members, short_sha))
    return sorted(set(findings))

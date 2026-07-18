"""Locate latest repository manifest artifacts on operator storage."""

from __future__ import annotations

import json
from pathlib import Path


def latest_repo_manifest_entries(manifest_dir: Path) -> dict[str, dict[str, object]]:
    """Return newest repo manifest payload per repo_key."""
    latest: dict[str, dict[str, object]] = {}
    if not manifest_dir.is_dir():
        return latest
    for path in sorted(manifest_dir.glob("*/*.repo_manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        repo_key = str(payload.get("repo_key") or "").strip()
        if not repo_key:
            continue
        generated_at = str(payload.get("generated_at") or "")
        existing = latest.get(repo_key)
        if existing is None or generated_at >= str(existing.get("generated_at") or ""):
            latest[repo_key] = payload
    return latest

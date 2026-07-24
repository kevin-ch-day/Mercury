"""Independent reconstruction checks for a pinned Git bundle."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def reconstruct_and_verify(bundle: Path, destination: Path, *, expected_commit: str, expected_tree: str, maintenance_sha256: str) -> dict[str, str | bool]:
    subprocess.run(["git", "clone", str(bundle), str(destination)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--detach", expected_commit], check=True, capture_output=True, text=True)
    head = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD^{tree}"], text=True).strip()
    status = subprocess.check_output(["git", "-C", str(destination), "status", "--porcelain"], text=True).strip()
    maintenance = destination / "src/database/db_query/virustotal_queries/reports/maintenance.py"
    digest = hashlib.sha256(maintenance.read_bytes()).hexdigest() if maintenance.is_file() else ""
    return {"head": head, "tree": tree, "clean": not status, "maintenance_sha256": digest,
            "head_match": head == expected_commit, "tree_match": tree == expected_tree,
            "maintenance_match": digest == maintenance_sha256}

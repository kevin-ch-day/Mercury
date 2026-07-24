"""Deterministic member contract for governed Erebus captures."""

from __future__ import annotations

from pathlib import PurePosixPath


REQUIRED = frozenset({
    "CAPTURE_REPORT.md", "capture_summary.json", "checksums.sha256", "checksums.sha256.verify",
    "manifest_receipt.json", "known_warnings.json", "runtime_restrictions.json",
    "phase3b_linkage.json", "supersession.json", "evidence/focused_tests.txt",
    "evidence/collection.txt", "evidence/compileall.txt", "evidence/git_diff_check.txt",
    "evidence/full_suite_summary.json", "evidence/dependency_validation.json",
    "reconstruction/reconstruction_result.txt", "reconstruction/reconstructed_identity.json",
    "reconstruction/reconstructed_maintenance_sha256.txt",
})
PREFIXES = ("artifacts/intake_contract/", "artifacts/source_recovery/", "git/", "ops/", "reconstruction/", "evidence/")
FORBIDDEN_PARTS = frozenset({".env", ".venv", ".venv-offline", "__pycache__", "output", "logs", "reports", "ScytaleDroid", "ObsidianDroid"})


def expected_bundle_name(short_sha: str) -> str:
    return f"git/erebus-engine-fedora_{short_sha}.bundle"


def validate_members(members: set[str], short_sha: str) -> list[str]:
    errors = sorted(REQUIRED - members)
    if expected_bundle_name(short_sha) not in members:
        errors.append("missing expected Git bundle")
    for member in members:
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts or any(part in FORBIDDEN_PARTS for part in path.parts):
            errors.append(f"forbidden member: {member}")
    return errors

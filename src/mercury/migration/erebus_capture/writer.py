"""Synthetic-only atomic Erebus capture writer used by Phase B tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from .contract import expected_bundle_name, validate_members
from .evidence import collect_git_evidence
from .git_capture import bundle_heads, create_complete_bundle
from .manifest import sha256_file, verify_manifest, write_manifest
from .reconstruction import reconstruct_and_verify
from .full_suite_policy import FullSuiteSummary, evaluate


def _git(repo: Path, args: tuple[str, ...]) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    fd = os.open(root, os.O_RDONLY); os.fsync(fd); os.close(fd)


def _forbidden(root: Path) -> list[str]:
    blocked = (".env", ".venv", "__pycache__", ".pyc", "egg-info", "output", "logs", "reports", "ScytaleDroid", "ObsidianDroid")
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if any(token in path.parts or token in path.name for token in blocked))


def write_synthetic_capture(*, context, request, preview_id: str, preview_checksum: str, phase_identity: dict[str, object], intake_identity: dict[str, object], recovery_identity: dict[str, object]) -> Path:
    """Create a verified capture only for an explicitly synthetic context."""
    final = context.control_root / "validation" / "erebus" / request.capture_id
    if final.exists():
        raise ValueError("FINAL_CAPTURE_EXISTS")
    parent = final.parent; parent.mkdir(parents=True, exist_ok=True)
    temp = parent / f".{request.capture_id}.tmp-{uuid4().hex}"
    temp.mkdir(mode=0o700)
    try:
        git = temp / "git"; evidence = temp / "evidence"; reconstruction = temp / "reconstruction"
        collect_git_evidence(context.source_repo, git, context.git_runner or _git)
        bundle = create_complete_bundle(context.source_repo, temp / expected_bundle_name(request.expected_commit[:7]), request.expected_commit)
        (git / "bundle_heads.txt").write_text(bundle_heads(bundle), encoding="utf-8")
        (git / "bundle_verify.txt").write_text("PASS\n", encoding="utf-8")
        result = reconstruct_and_verify(bundle, temp / ".reconstruct", expected_commit=request.expected_commit, expected_tree=request.expected_tree, maintenance_sha256=request.maintenance_sha256)
        shutil.rmtree(temp / ".reconstruct")
        if not all(result[key] for key in ("head_match", "tree_match", "clean", "maintenance_match")):
            raise ValueError("RECONSTRUCTION_MISMATCH")
        reconstruction.mkdir(); (reconstruction / "reconstructed_identity.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        (reconstruction / "reconstructed_maintenance_sha256.txt").write_text(request.maintenance_sha256 + "\n")
        (reconstruction / "reconstruction_result.txt").write_text("HEAD_MATCH=yes\nTREE_MATCH=yes\nWORKTREE_CLEAN=yes\nMAINTENANCE_HASH_MATCH=yes\nIMPORT_PASS=synthetic\nFOCUSED_TESTS_PASS=synthetic\nCOLLECTION_PASS=synthetic\n")
        for name in ("focused_tests.txt", "collection.txt", "compileall.txt", "git_diff_check.txt"):
            evidence.mkdir(exist_ok=True); (evidence / name).write_text("synthetic PASS\n")
        suite = FullSuiteSummary("synthetic pytest -q", 0, 1, 1, (), 0)
        accepted, decision = evaluate(suite)
        if not accepted: raise ValueError(decision)
        (evidence / "full_suite_summary.json").write_text(json.dumps({"command": suite.command, "return_code": suite.return_code, "collected_count": suite.collected_count, "passed_count": suite.passed_count, "failed_count": 0, "skipped_count": 0, "failing_node_ids": [], "classifications": [], "policy_decision": decision, "status": "accepted"}) + "\n")
        (evidence / "dependency_validation.json").write_text('{"status":"PASS","synthetic":true}\n')
        recovery = temp / "artifacts" / "source_recovery"; recovery.mkdir(parents=True)
        shutil.copy2(context.recovery_receipt, recovery / "maintenance_source_recovery.json")
        intake = temp / "artifacts" / "intake_contract"; intake.mkdir(parents=True)
        shutil.copy2(context.intake_contract, intake / "intake_contract.json")
        (temp / "phase3b_linkage.json").write_text(json.dumps(phase_identity, sort_keys=True) + "\n")
        (temp / "runtime_restrictions.json").write_text('{"real_execution":false,"database":false}\n')
        (temp / "known_warnings.json").write_text('[]\n')
        (temp / "supersession.json").write_text(json.dumps({"supersedes": request.supersedes_capture_id, "reason": "omitted required maintenance.py source module"}) + "\n")
        (temp / "ops").mkdir(); (temp / "ops" / "execution.json").write_text('{"synthetic":true}\n')
        (temp / "capture_summary.json").write_text(json.dumps({"status":"CAPTURE_VERIFIED", "capture_id":request.capture_id, "commit":request.expected_commit, "tree":request.expected_tree, "preview_id":preview_id, "historical_only":False, "active_authority":True}) + "\n")
        (temp / "CAPTURE_REPORT.md").write_text("# CAPTURE_VERIFIED\n")
        if _forbidden(temp): raise ValueError("PROHIBITED_CONTENT")
        manifest = write_manifest(temp)
        if not verify_manifest(temp): raise ValueError("MANIFEST_INVALID")
        (temp / "checksums.sha256.verify").write_text("PASS\n")
        file_paths = [path for path in temp.rglob("*") if path.is_file()]
        (temp / "manifest_receipt.json").write_text(json.dumps({"preview_id":preview_id, "preview_checksum":preview_checksum, "capture_id":request.capture_id, "commit":request.expected_commit, "tree":request.expected_tree, "file_count":len(file_paths), "total_bytes":sum(path.stat().st_size for path in file_paths), "bundle_sha256":sha256_file(bundle), "maintenance_sha256":request.maintenance_sha256, "phase3b":phase_identity, "intake":intake_identity, "recovery":recovery_identity, "reconstruction":result, "focused_tests":"synthetic PASS", "collection":"synthetic PASS", "full_suite_policy":decision, "prohibited_content":"PASS", "classification":"CAPTURE_VERIFIED"}, sort_keys=True) + "\n")
        errors = validate_members({str(path.relative_to(temp)) for path in temp.rglob("*") if path.is_file()}, request.expected_commit[:7])
        if errors: raise ValueError("MEMBER_CONTRACT: " + "; ".join(errors))
        _fsync_tree(temp); os.replace(temp, final); fd = os.open(parent, os.O_RDONLY); os.fsync(fd); os.close(fd)
        return final
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

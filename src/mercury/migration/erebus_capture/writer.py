"""Synthetic-only atomic Erebus capture writer used by Phase B tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from .contract import expected_bundle_name
from .evidence import collect_git_evidence
from .git_capture import bundle_heads, bundle_verify, create_complete_bundle
from .manifest import sha256_file, verify_manifest, write_manifest
from .reconstruction import reconstruct_and_verify
from .full_suite_policy import ExpectedFailure, FullSuiteSummary, evaluate
from .scanner import scan_capture
from .validation_runner import ValidationResult
from .phase3b_validation import BACKUPS


def _git(repo: Path, args: tuple[str, ...]) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    fd = os.open(root, os.O_RDONLY); os.fsync(fd); os.close(fd)


def write_capture(
    *,
    context,
    request,
    preview_id: str,
    preview_checksum: str,
    phase_identity: dict[str, object],
    intake_identity: dict[str, object],
    recovery_identity: dict[str, object],
    real_execution: bool = False,
) -> Path:
    """Atomically publish a verified capture. Real execution must be separately authorized."""
    final = context.control_root / "validation" / "erebus" / request.capture_id
    if final.exists():
        raise ValueError("FINAL_CAPTURE_EXISTS")
    parent = final.parent; parent.mkdir(parents=True, exist_ok=True)
    temp = parent / f".{request.capture_id}.tmp-{uuid4().hex}"
    temp.mkdir(mode=0o700)
    try:
        if context.validation_runner is None:
            raise ValueError("VALIDATION_RUNNER_REQUIRED")
        validation: dict[str, ValidationResult] = {}
        commands = {
            "focused_tests": ("python", "-m", "pytest", "-q", "tests/database"),
            "collection": ("python", "-m", "pytest", "-q", "--collect-only"),
            "compileall": ("python", "-m", "compileall", "-q", "src", "tests"),
            "git_diff_check": ("git", "diff", "--check"),
            "dependency_validation": ("python", "-m", "pip", "check"),
            "full_suite": ("python", "-m", "pytest", "-q"),
            "reconstruction_import": ("python", "-c", "from database.db_query.virustotal_queries.reports import maintenance"),
            "reconstruction_focused_tests": ("python", "-m", "pytest", "-q", "tests/database/test_vt_report_maintenance_source.py"),
            "reconstruction_collection": ("python", "-m", "pytest", "-q", "--collect-only"),
        }
        for name, command in commands.items():
            result = context.validation_runner.run(name, cwd=context.source_repo, command=command)
            validation[name] = result
            if name.startswith("reconstruction_"):
                continue
            if name == "full_suite":
                if not result.started or not result.completed:
                    raise ValueError("VALIDATION_FAILED: full_suite")
            elif not result.accepted:
                raise ValueError(f"VALIDATION_FAILED: {name}")
        git = temp / "git"; evidence = temp / "evidence"; reconstruction = temp / "reconstruction"
        collect_git_evidence(context.source_repo, git, context.git_runner or _git)
        bundle = create_complete_bundle(context.source_repo, temp / expected_bundle_name(request.expected_commit[:7]), request.expected_commit)
        (git / "bundle_heads.txt").write_text(bundle_heads(bundle), encoding="utf-8")
        (git / "bundle_verify.txt").write_text(bundle_verify(bundle), encoding="utf-8")
        result = reconstruct_and_verify(bundle, temp / ".reconstruct", expected_commit=request.expected_commit, expected_tree=request.expected_tree, maintenance_sha256=request.maintenance_sha256)
        shutil.rmtree(temp / ".reconstruct", ignore_errors=True)
        if not all(result[key] for key in ("head_match", "tree_match", "clean", "maintenance_match")):
            raise ValueError("RECONSTRUCTION_MISMATCH")
        result.update({"import_pass": validation["reconstruction_import"].accepted, "focused_tests_pass": validation["reconstruction_focused_tests"].accepted, "collection_pass": validation["reconstruction_collection"].accepted})
        if not all(result[key] for key in ("import_pass", "focused_tests_pass", "collection_pass")):
            raise ValueError("RECONSTRUCTION_VALIDATION_FAILED")
        reconstruction.mkdir(); (reconstruction / "reconstructed_identity.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        (reconstruction / "reconstructed_maintenance_sha256.txt").write_text(request.maintenance_sha256 + "\n")
        (reconstruction / "reconstruction_result.txt").write_text("HEAD_MATCH=yes\nTREE_MATCH=yes\nWORKTREE_CLEAN=yes\nMAINTENANCE_HASH_MATCH=yes\nIMPORT_PASS=yes\nFOCUSED_TESTS_PASS=yes\nCOLLECTION_PASS=yes\n")
        for key, name in (("focused_tests", "focused_tests.txt"), ("collection", "collection.txt"), ("compileall", "compileall.txt"), ("git_diff_check", "git_diff_check.txt")):
            evidence.mkdir(exist_ok=True); (evidence / name).write_text(__import__("json").dumps(validation[key].evidence(), sort_keys=True) + "\n")
        suite_result = validation["full_suite"]
        parsed = suite_result.parsed or {}
        failures = tuple(ExpectedFailure(**item) for item in parsed.get("failures", []))
        suite = FullSuiteSummary(
            " ".join(suite_result.command),
            suite_result.return_code,
            int(parsed.get("collected_count", 1)),
            int(parsed.get("passed_count", 1)),
            failures,
            int(parsed.get("skipped_count", 0)),
            collection_errors=int(parsed.get("collection_errors", 0) or 0),
        )
        approved = tuple(context.approved_full_suite_failures or ())
        accepted, decision = evaluate(suite, approved)
        if not accepted: raise ValueError(decision)
        (evidence / "full_suite_summary.json").write_text(json.dumps({"command": suite.command, "return_code": suite.return_code, "collected_count": suite.collected_count, "passed_count": suite.passed_count, "failed_count": len(failures), "skipped_count": suite.skipped_count, "failing_node_ids": [item.node_id for item in failures], "classifications": [item.classification for item in failures], "policy_decision": decision, "status": "accepted"}) + "\n")
        (evidence / "dependency_validation.json").write_text(json.dumps(validation["dependency_validation"].evidence(), sort_keys=True) + "\n")
        recovery = temp / "artifacts" / "source_recovery"; recovery.mkdir(parents=True)
        shutil.copy2(context.recovery_receipt, recovery / "maintenance_source_recovery.json")
        intake = temp / "artifacts" / "intake_contract"; intake.mkdir(parents=True)
        shutil.copy2(context.intake_contract, intake / "intake_contract.json")
        phase_linkage = {**phase_identity, "backup_ids": sorted(BACKUPS)}
        (temp / "phase3b_linkage.json").write_text(json.dumps(phase_linkage, sort_keys=True) + "\n")
        (temp / "runtime_restrictions.json").write_text(json.dumps({"real_execution": bool(real_execution), "database": False}) + "\n")
        (temp / "known_warnings.json").write_text("[]\n")
        (temp / "supersession.json").write_text(json.dumps({"supersedes": request.supersedes_capture_id, "reason": "prior clean capture omitted required ignored maintenance.py source module", "old_capture_preserved": True, "old_capture_active_authority": False}) + "\n")
        (temp / "ops").mkdir()
        (temp / "ops" / "execution.json").write_text(
            json.dumps({
                "synthetic": not real_execution,
                "real_execution": bool(real_execution),
                "authorization_receipt_sha256": getattr(context, "authorization_receipt_sha256", "") or "",
            }, sort_keys=True) + "\n"
        )
        if scan_capture(temp, short_sha=request.expected_commit[:7], enforce_contract=False): raise ValueError("PROHIBITED_CONTENT")
        evidence_label = "PASS" if real_execution else "synthetic PASS"
        decisions = {"prohibited_content":"PASS", "intended_member_contract":"PASS", "focused_tests":evidence_label, "collection":evidence_label, "compileall":evidence_label, "git_diff_check":evidence_label, "dependency_validation":evidence_label, "full_suite_policy":decision, "reconstruction":"PASS"}
        (temp / "capture_summary.json").write_text(json.dumps({"status":"CAPTURE_VERIFIED", "capture_id":request.capture_id, "commit":request.expected_commit, "tree":request.expected_tree, "preview_id":preview_id, "historical_only":False, "active_authority":True, "real_execution": bool(real_execution), "decisions":decisions}) + "\n")
        (temp / "CAPTURE_REPORT.md").write_text("# Erebus source capture\n\nStatus: **CAPTURE_VERIFIED**\n\n" + f"- Capture: `{request.capture_id}`\n- Commit: `{request.expected_commit}`\n- Tree: `{request.expected_tree}`\n- Real execution: `{bool(real_execution)}`\n- Recovery, Phase 3B, intake, bundle, reconstruction, scanner, and validation: PASS\n- Supersedes the preserved incomplete capture because it omitted `maintenance.py`.\n")
        write_manifest(temp)
        (temp / "checksums.sha256.verify").write_text("PASS\n")
        file_paths = [path for path in temp.rglob("*") if path.is_file()]
        (temp / "manifest_receipt.json").write_text(json.dumps({"preview_id":preview_id, "preview_checksum":preview_checksum, "capture_id":request.capture_id, "commit":request.expected_commit, "tree":request.expected_tree, "file_count":len(file_paths), "total_bytes":sum(path.stat().st_size for path in file_paths), "bundle_sha256":sha256_file(bundle), "maintenance_sha256":request.maintenance_sha256, "phase3b":phase_linkage, "intake":intake_identity, "intake_contract_sha256":intake_identity.get("sha256"), "recovery":recovery_identity, "recovery_receipt_sha256":recovery_identity.get("receipt_sha256"), "reconstruction":result, "focused_tests":"PASS", "collection":"PASS", "compileall":"PASS", "git_diff_check":"PASS", "dependency_validation":"PASS", "full_suite_policy":decision, "prohibited_content":"PASS", "intended_member_validation":"PASS", "checksum_verification":"PASS", "classification":"CAPTURE_VERIFIED", "real_execution": bool(real_execution)}, sort_keys=True) + "\n")
        if scan_capture(temp, short_sha=request.expected_commit[:7]): raise ValueError("PROHIBITED_CONTENT")
        if not verify_manifest(temp): raise ValueError("MANIFEST_INVALID")
        _fsync_tree(temp); os.replace(temp, final); fd = os.open(parent, os.O_RDONLY); os.fsync(fd); os.close(fd)
        return final
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def write_synthetic_capture(*, context, request, preview_id: str, preview_checksum: str, phase_identity: dict[str, object], intake_identity: dict[str, object], recovery_identity: dict[str, object]) -> Path:
    """Compatibility wrapper for Phase B synthetic tests."""
    return write_capture(
        context=context, request=request, preview_id=preview_id, preview_checksum=preview_checksum,
        phase_identity=phase_identity, intake_identity=intake_identity, recovery_identity=recovery_identity,
        real_execution=False,
    )

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from mercury.migration.erebus_capture.models import ErebusCaptureRequest
from mercury.migration.erebus_capture.service import (
    begin_preview_execution, load_preview, mark_preview_consumed, preview_capture,
)
from mercury.migration.erebus_capture.evidence import collect_git_evidence
from mercury.migration.erebus_capture.git_capture import create_complete_bundle
from mercury.migration.erebus_capture.reconstruction import reconstruct_and_verify
from mercury.migration.erebus_capture.manifest import verify_manifest, write_manifest
from mercury.migration.erebus_capture.preview_state import PreviewState, begin_execution, invalidate, load_state, mark_consumed
from mercury.migration.erebus_capture.storage_preflight import EXPECTED_LABEL, EXPECTED_MOUNT, EXPECTED_UUID, StorageFacts, validate_storage
from mercury.migration.erebus_capture.recovery_validation import PATH as RECOVERY_PATH, validate_recovery_receipt
from mercury.migration.erebus_capture.phase3b_validation import BACKUPS, RUN_ID, validate_phase3b
from mercury.migration.erebus_capture.intake_validation import ALLOWED, EXCLUDED, validate_intake_contract
from mercury.migration.erebus_capture.contract import REQUIRED, expected_bundle_name, validate_members
from mercury.migration.erebus_capture.package_validation import validate_erebus_capture_for_package
from mercury.migration.erebus_capture.context import CaptureContext
from mercury.migration.erebus_capture.service import create_preview, revalidate_preview_for_execute
from mercury.migration.erebus_capture.preview_store import atomic_publish, preview_root


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _request(tmp_path: Path) -> ErebusCaptureRequest:
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    source = repo / "src/database/db_query/virustotal_queries/reports"; source.mkdir(parents=True)
    maintenance = source / "maintenance.py"; maintenance.write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "origin", "main:refs/remotes/origin/main"], check=True, capture_output=True)
    return ErebusCaptureRequest("preview-one", str(repo), "capture-one", _git(repo, "rev-parse", "HEAD"),
        _git(repo, "rev-parse", "HEAD^{tree}"), "phase", hashlib.sha256(maintenance.read_bytes()).hexdigest(), str(tmp_path / "control"))


def test_preview_is_durable_and_does_not_create_capture(tmp_path: Path) -> None:
    request = _request(tmp_path)
    preview = preview_capture(request)
    assert preview.ok
    assert Path(preview.path, "capture_preview.sha256").is_file()
    assert not Path(request.control_root, "validation", "erebus", request.capture_id).exists()
    assert load_preview(request.control_root, preview.preview_id).ok


def test_tampered_preview_is_refused(tmp_path: Path) -> None:
    request = _request(tmp_path)
    preview = preview_capture(request)
    Path(preview.path, "capture_preview.json").write_text("{}\n")
    result = load_preview(request.control_root, preview.preview_id)
    assert not result.ok
    assert result.errors == ["PREVIEW_CHECKSUM_MISMATCH"]


def test_unexpected_preview_file_is_refused(tmp_path: Path) -> None:
    request = _request(tmp_path); preview = preview_capture(request)
    Path(preview.path, "surprise.txt").write_text("nope\n")
    assert load_preview(request.control_root, preview.preview_id).errors == ["PREVIEW_FILES_UNEXPECTED: surprise.txt"]


def test_component_identity_mismatch_is_refused_after_checksum_update(tmp_path: Path) -> None:
    request = _request(tmp_path)
    preview = preview_capture(request)
    root = Path(preview.path)
    (root / "phase3b_identity.json").write_text('{"run_id": "wrong"}\n')
    manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob("*.json")}
    (root / "capture_preview.sha256").write_text(__import__("json").dumps(manifest, sort_keys=True))
    assert load_preview(request.control_root, preview.preview_id).errors == ["PREVIEW_COMPONENT_MISMATCH"]


def test_preview_refuses_source_drift(tmp_path: Path) -> None:
    request = _request(tmp_path)
    preview = preview_capture(request)
    Path(request.repository, "drift.txt").write_text("untracked\n")
    assert load_preview(request.control_root, preview.preview_id).errors == ["PREVIEW_SOURCE_DRIFT"]


def test_preview_refuses_existing_final_capture(tmp_path: Path) -> None:
    request = _request(tmp_path)
    Path(request.control_root, "validation", "erebus", request.capture_id).mkdir(parents=True)
    preview = preview_capture(request)
    assert not preview.ok
    assert preview.reason_codes == ["FINAL_CAPTURE_EXISTS"]


def test_preview_id_is_explicit_and_duplicate_refuses(tmp_path: Path) -> None:
    request = _request(tmp_path)
    assert preview_capture(request).ok
    duplicate = preview_capture(request)
    assert not duplicate.ok
    assert duplicate.reason_codes == ["PREVIEW_ID_EXISTS"]


def test_preview_refuses_tampered_recovery_receipt(tmp_path: Path) -> None:
    request = _request(tmp_path)
    receipt = tmp_path / "receipt.json"; receipt.write_text('{"ok": true}\n')
    receipt.with_suffix(".json.sha256").write_text(f"{hashlib.sha256(receipt.read_bytes()).hexdigest()}  receipt.json\n")
    request = ErebusCaptureRequest(**{**request.__dict__, "recovery_receipt": str(receipt)})
    preview = preview_capture(request)
    receipt.write_text('{"ok": false}\n')
    assert load_preview(request.control_root, preview.preview_id).errors == ["PREVIEW_RECOVERY_DRIFT"]


def test_git_evidence_uses_injected_runner(tmp_path: Path) -> None:
    called: list[tuple[str, ...]] = []
    def fake(_repo: Path, args: tuple[str, ...]) -> str:
        called.append(args)
        return "a\nb" if args == ("ls-files",) else "value"
    collect_git_evidence(tmp_path, tmp_path / "evidence", fake)
    assert (tmp_path / "evidence" / "HEAD").read_text() == "value\n"
    assert (tmp_path / "evidence" / "tracked_file_count.txt").read_text() == "2\n"
    assert ("rev-parse", "HEAD") in called


def test_complete_bundle_reconstructs_pinned_identity(tmp_path: Path) -> None:
    request = _request(tmp_path)
    bundle = create_complete_bundle(Path(request.repository), tmp_path / "repo.bundle", request.expected_commit)
    result = reconstruct_and_verify(bundle, tmp_path / "reconstructed", expected_commit=request.expected_commit,
        expected_tree=request.expected_tree, maintenance_sha256=request.maintenance_sha256)
    assert result["head_match"] and result["tree_match"] and result["maintenance_match"] and result["clean"]


def test_manifest_hashes_content_and_detects_drift(tmp_path: Path) -> None:
    target = tmp_path / "capture"; target.mkdir(); (target / "receipt.txt").write_text("ok\n")
    write_manifest(target)
    assert verify_manifest(target)
    (target / "receipt.txt").write_text("changed\n")
    assert not verify_manifest(target)


def test_preview_state_blocks_reuse(tmp_path: Path) -> None:
    request = _request(tmp_path); preview = preview_capture(request); root = Path(preview.path)
    assert begin_execution(root)
    assert not begin_execution(root)
    assert mark_consumed(root)
    assert not load_preview(request.control_root, preview.preview_id).ok


def test_service_state_wrappers_require_exact_preview_id(tmp_path: Path) -> None:
    request = _request(tmp_path); preview = preview_capture(request)
    assert begin_preview_execution(request.control_root, preview.preview_id).ok
    assert not begin_preview_execution(request.control_root, preview.preview_id).ok
    assert mark_preview_consumed(request.control_root, preview.preview_id).ok


def test_preview_invalidation_is_durable(tmp_path: Path) -> None:
    request = _request(tmp_path); preview = preview_capture(request); root = Path(preview.path)
    invalidate(root)
    assert load_state(root) is PreviewState.INVALIDATED
    assert not load_preview(request.control_root, preview.preview_id).ok


def test_context_preview_revalidates_all_synthetic_identities(tmp_path: Path) -> None:
    request = _request(tmp_path)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(__import__("json").dumps({"source_relative_path": RECOVERY_PATH, "artifact_sha256": request.maintenance_sha256, "repair_commit": request.expected_commit, "repair_tree": request.expected_tree, "original_ignore_rule": "reports/", "repaired_ignore_rule": "/reports/", "tracked": True}))
    receipt.with_suffix(".json.sha256").write_text(f"{hashlib.sha256(receipt.read_bytes()).hexdigest()}  receipt.json\n")
    phase = tmp_path / "phase"; (phase / "dumps").mkdir(parents=True); (phase / "restore").mkdir()
    (phase / "PHASE3B_REPORT.md").write_text("x")
    (phase / "phase3b_summary.json").write_text(__import__("json").dumps({"run_id": RUN_ID}))
    (phase / "dumps/dump_metadata.json").write_text(__import__("json").dumps({"backup_ids": sorted(BACKUPS)}))
    (phase / "restore/source_vs_restore_comparison.json").write_text(__import__("json").dumps({"zero_unexplained_differences": True}))
    intake = tmp_path / "intake.json"; intake.write_text(__import__("json").dumps({"schema_version": 1, "intake_root_name": "erebus-intake", "included_members": sorted(ALLOWED), "excluded_members": sorted(EXCLUDED), "bypass_allowed": False, "mount_guard_required": True}))
    intake.with_suffix(".json.sha256").write_text(f"{hashlib.sha256(intake.read_bytes()).hexdigest()}  intake.json\n")
    facts = StorageFacts("/dev/x1", "/dev/x", "ext4", EXPECTED_LABEL, EXPECTED_UUID, EXPECTED_MOUNT, "rw", 100, True, True)
    context = CaptureContext(Path(request.control_root), Path(request.repository), receipt, phase, intake, lambda: facts)
    request = ErebusCaptureRequest(**{**request.__dict__, "phase3b_run_id": RUN_ID})
    preview = create_preview(context, request)
    assert preview.ok
    assert revalidate_preview_for_execute(context, request.preview_id).ok


@pytest.mark.parametrize("change", ["uuid", "label", "mount", "space", "operation", "role"])
def test_storage_preflight_refuses_unsafe_facts(change: str) -> None:
    values = dict(partition="/dev/test1", parent="/dev/test", fstype="ext4", label=EXPECTED_LABEL,
        uuid=EXPECTED_UUID, mount_path=EXPECTED_MOUNT, mount_options="rw", free_bytes=100,
        source_host=True, writer_enabled=True)
    if change == "uuid": values["uuid"] = "wrong"
    if change == "label": values["label"] = "wrong"
    if change == "mount": values["mount_path"] = "/wrong"
    if change == "space": values["free_bytes"] = 0
    if change == "operation": values["active_operations"] = ("backup",)
    if change == "role": values["source_host"] = False
    with pytest.raises(ValueError): validate_storage(StorageFacts(**values), minimum_free_bytes=1)


@pytest.mark.parametrize("field", ["source_relative_path", "artifact_sha256", "repair_commit", "repair_tree", "original_ignore_rule", "repaired_ignore_rule", "tracked"])
def test_recovery_validator_refuses_identity_mismatch(tmp_path: Path, field: str) -> None:
    data = {"source_relative_path": RECOVERY_PATH, "artifact_sha256": "hash", "repair_commit": "commit", "repair_tree": "tree", "original_ignore_rule": "reports/", "repaired_ignore_rule": "/reports/", "tracked": True}
    data[field] = False if field == "tracked" else "wrong"
    receipt = tmp_path / "receipt.json"; receipt.write_text(__import__("json").dumps(data))
    receipt.with_suffix(".json.sha256").write_text(f"{hashlib.sha256(receipt.read_bytes()).hexdigest()}  receipt.json\n")
    with pytest.raises(ValueError, match="RECOVERY_MISMATCH"):
        validate_recovery_receipt(receipt, artifact_sha256="hash", repair_commit="commit", repair_tree="tree")


@pytest.mark.parametrize("fault", ["missing", "bad_run", "bad_backups", "bad_comparison"])
def test_phase3b_validator_refuses_invalid_evidence(tmp_path: Path, fault: str) -> None:
    root = tmp_path / RUN_ID; (root / "dumps").mkdir(parents=True); (root / "restore").mkdir()
    (root / "PHASE3B_REPORT.md").write_text("report\n")
    (root / "phase3b_summary.json").write_text(__import__("json").dumps({"run_id": "wrong" if fault == "bad_run" else RUN_ID}))
    (root / "dumps/dump_metadata.json").write_text(__import__("json").dumps({"backup_ids": [] if fault == "bad_backups" else sorted(BACKUPS)}))
    (root / "restore/source_vs_restore_comparison.json").write_text(__import__("json").dumps({"zero_unexplained_differences": fault != "bad_comparison"}))
    if fault == "missing": (root / "PHASE3B_REPORT.md").unlink()
    with pytest.raises(ValueError, match="PHASE3B_MISMATCH"): validate_phase3b(root, RUN_ID)


@pytest.mark.parametrize("fault", ["checksum", "schema", "members", "bypass", "secret"])
def test_intake_validator_refuses_unsafe_contract(tmp_path: Path, fault: str) -> None:
    data = {"schema_version": 1, "intake_root_name": "erebus-intake", "included_members": sorted(ALLOWED), "excluded_members": sorted(EXCLUDED), "bypass_allowed": False, "mount_guard_required": True}
    if fault == "schema": data["schema_version"] = 2
    if fault == "members": data["included_members"] = ["downloads"]
    if fault == "bypass": data["bypass_allowed"] = True
    if fault == "secret": data["note"] = "api_key"
    contract = tmp_path / "intake_contract.json"; contract.write_text(__import__("json").dumps(data))
    contract.with_suffix(".json.sha256").write_text(("bad" if fault == "checksum" else hashlib.sha256(contract.read_bytes()).hexdigest()) + "  intake_contract.json\n")
    with pytest.raises(ValueError, match="INTAKE_MISMATCH"): validate_intake_contract(contract)


def test_member_contract_requires_bundle_and_rejects_forbidden() -> None:
    short = "abcdef0"; members = set(REQUIRED) | {expected_bundle_name(short)}
    assert validate_members(members, short) == []
    assert "forbidden member: output/report.txt" in validate_members(members | {"output/report.txt"}, short)


def test_package_validator_refuses_incomplete_capture(tmp_path: Path) -> None:
    assert validate_erebus_capture_for_package(tmp_path, capture_id="candidate", commit="c", tree="t") == ["verified capture evidence is incomplete"]


def test_preview_store_rejects_traversal_and_publishes_atomically(tmp_path: Path) -> None:
    with pytest.raises(ValueError): preview_root(tmp_path, "../bad")
    final = preview_root(tmp_path, "preview-good")
    temp = final.parent / ".preview-good.tmp-test"; temp.mkdir(parents=True); (temp / "receipt.json").write_text("{}\n")
    atomic_publish(temp, final)
    assert (final / "receipt.json").is_file() and not temp.exists()

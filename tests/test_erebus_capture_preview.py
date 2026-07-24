from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.migration.erebus_capture.models import ErebusCaptureRequest
from mercury.migration.erebus_capture.service import (
    begin_preview_execution, execute_capture, load_preview, mark_preview_consumed, preview_capture,
)
from mercury.migration.erebus_capture.evidence import collect_git_evidence
from mercury.migration.erebus_capture.git_capture import bundle_verify, create_complete_bundle
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
from mercury.migration.erebus_capture.validation_runner import DeterministicValidationRunner, ValidationResult
from mercury.migration.erebus_capture.service import (
    build_preview_payload, create_preview, publish_preview, revalidate_preview_for_execute,
)
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
    assert request.expected_commit in bundle_verify(bundle)


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


def test_context_preview_revalidates_all_synthetic_identities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    context = CaptureContext(Path(request.control_root), Path(request.repository), receipt, phase, intake, lambda: facts,
                             allow_synthetic_execution=True, validation_runner=DeterministicValidationRunner())
    request = ErebusCaptureRequest(**{**request.__dict__, "phase3b_run_id": RUN_ID})
    payload = build_preview_payload(context, request)
    assert not (Path(request.control_root) / "validation" / "previews" / "erebus" / request.preview_id).exists()
    assert not (Path(request.control_root) / "validation" / "erebus" / request.capture_id).exists()
    preview = publish_preview(payload)
    assert preview.ok
    assert revalidate_preview_for_execute(context, request.preview_id).ok
    created = create_preview(context, ErebusCaptureRequest(**{
        **request.__dict__, "preview_id": "preview-two", "capture_id": "capture-two",
    }))
    assert created.ok
    executed = execute_capture(context, "preview-two")
    assert executed.ok, executed.errors
    assert validate_erebus_capture_for_package(Path(request.control_root), capture_id="capture-two",
                                               commit=request.expected_commit, tree=request.expected_tree) == []
    capture = Path(request.control_root) / "validation" / "erebus" / "capture-two"
    summary = __import__("json").loads((capture / "capture_summary.json").read_text())
    manifest_receipt = __import__("json").loads((capture / "manifest_receipt.json").read_text())
    assert summary["decisions"]["prohibited_content"] == "PASS"
    assert manifest_receipt["prohibited_content"] == "PASS"
    assert "CAPTURE_VERIFIED" in (capture / "CAPTURE_REPORT.md").read_text()
    failed = create_preview(context, ErebusCaptureRequest(**{
        **request.__dict__, "preview_id": "preview-failed", "capture_id": "capture-failed",
    }))
    assert failed.ok
    from mercury.migration.erebus_capture import writer
    monkeypatch.setattr(writer, "write_synthetic_capture", lambda **_kwargs: (_ for _ in ()).throw(ValueError("injected writer failure")))
    refusal = execute_capture(context, "preview-failed")
    assert not refusal.ok
    assert load_state(Path(request.control_root) / "validation" / "previews" / "erebus" / "preview-failed") is PreviewState.REFUSED
    assert not (Path(request.control_root) / "validation" / "erebus" / "capture-failed").exists()
    from typer.testing import CliRunner
    from mercury.cli import app
    facts_path = tmp_path / "storage-facts.json"
    facts_path.write_text(__import__("json").dumps(facts.__dict__))
    cli = CliRunner().invoke(app, [
        "migration", "capture-erebus-source", "preview", "--preview-id", "preview-cli",
        "--repo", str(request.repository), "--capture-id", "capture-cli",
        "--expected-commit", request.expected_commit, "--expected-tree", request.expected_tree,
        "--phase3b-run-id", RUN_ID, "--maintenance-sha256", request.maintenance_sha256,
        "--recovery-receipt", str(receipt), "--phase3b-root", str(phase),
        "--intake-contract", str(intake), "--control-root", str(request.control_root),
        "--storage-facts", str(facts_path),
    ])
    assert cli.exit_code == 0, cli.output
    assert "PREVIEW READY" in cli.output


def test_storage_preflight_refuses_unsafe_facts() -> None:
    base = dict(
        partition="/dev/test1", parent="/dev/test", fstype="ext4", label=EXPECTED_LABEL,
        uuid=EXPECTED_UUID, mount_path=EXPECTED_MOUNT, mount_options="rw", free_bytes=100,
        source_host=True, writer_enabled=True,
    )
    mutations = (
        {"uuid": "wrong"},
        {"label": "wrong"},
        {"mount_path": "/wrong"},
        {"free_bytes": 0},
        {"active_operations": ("backup",)},
        {"source_host": False},
    )
    for mutation in mutations:
        values = {**base, **mutation}
        with pytest.raises(ValueError):
            validate_storage(StorageFacts(**values), minimum_free_bytes=1)


def test_recovery_validator_refuses_identity_mismatch(tmp_path: Path) -> None:
    fields = (
        "source_relative_path", "artifact_sha256", "repair_commit", "repair_tree",
        "original_ignore_rule", "repaired_ignore_rule", "tracked",
    )
    for field in fields:
        data = {
            "source_relative_path": RECOVERY_PATH, "artifact_sha256": "hash",
            "repair_commit": "commit", "repair_tree": "tree",
            "original_ignore_rule": "reports/", "repaired_ignore_rule": "/reports/", "tracked": True,
        }
        data[field] = False if field == "tracked" else "wrong"
        receipt = tmp_path / f"receipt-{field}.json"
        receipt.write_text(__import__("json").dumps(data))
        receipt.with_suffix(".json.sha256").write_text(
            f"{hashlib.sha256(receipt.read_bytes()).hexdigest()}  {receipt.name}\n"
        )
        with pytest.raises(ValueError, match="RECOVERY_MISMATCH"):
            validate_recovery_receipt(receipt, artifact_sha256="hash", repair_commit="commit", repair_tree="tree")


def test_phase3b_validator_refuses_invalid_evidence(tmp_path: Path) -> None:
    for fault in ("missing", "bad_run", "bad_backups", "bad_comparison"):
        root = tmp_path / fault / RUN_ID
        (root / "dumps").mkdir(parents=True)
        (root / "restore").mkdir()
        (root / "PHASE3B_REPORT.md").write_text("report\n")
        (root / "phase3b_summary.json").write_text(
            __import__("json").dumps({"run_id": "wrong" if fault == "bad_run" else RUN_ID})
        )
        (root / "dumps/dump_metadata.json").write_text(
            __import__("json").dumps({"backup_ids": [] if fault == "bad_backups" else sorted(BACKUPS)})
        )
        (root / "restore/source_vs_restore_comparison.json").write_text(
            __import__("json").dumps({"zero_unexplained_differences": fault != "bad_comparison"})
        )
        if fault == "missing":
            (root / "PHASE3B_REPORT.md").unlink()
        with pytest.raises(ValueError, match="PHASE3B_MISMATCH"):
            validate_phase3b(root, RUN_ID)


def test_intake_validator_refuses_unsafe_contract(tmp_path: Path) -> None:
    for fault in ("checksum", "schema", "members", "bypass", "secret"):
        data = {
            "schema_version": 1, "intake_root_name": "erebus-intake",
            "included_members": sorted(ALLOWED), "excluded_members": sorted(EXCLUDED),
            "bypass_allowed": False, "mount_guard_required": True,
        }
        if fault == "schema":
            data["schema_version"] = 2
        if fault == "members":
            data["included_members"] = ["downloads"]
        if fault == "bypass":
            data["bypass_allowed"] = True
        if fault == "secret":
            data["note"] = "api_key"
        contract = tmp_path / f"intake_contract_{fault}.json"
        contract.write_text(__import__("json").dumps(data))
        digest = "bad" if fault == "checksum" else hashlib.sha256(contract.read_bytes()).hexdigest()
        contract.with_suffix(".json.sha256").write_text(f"{digest}  {contract.name}\n")
        with pytest.raises(ValueError, match="INTAKE_MISMATCH"):
            validate_intake_contract(contract)


def test_member_contract_requires_bundle_and_rejects_forbidden() -> None:
    short = "abcdef0"; members = set(REQUIRED) | {expected_bundle_name(short)}
    assert validate_members(members, short) == []
    assert "forbidden member: output/report.txt" in validate_members(members | {"output/report.txt"}, short)


def test_preview_store_rejects_traversal_and_publishes_atomically(tmp_path: Path) -> None:
    with pytest.raises(ValueError): preview_root(tmp_path, "../bad")
    final = preview_root(tmp_path, "preview-good")
    temp = final.parent / ".preview-good.tmp-test"; temp.mkdir(parents=True); (temp / "receipt.json").write_text("{}\n")
    atomic_publish(temp, final)
    assert (final / "receipt.json").is_file() and not temp.exists()


def test_menu_preview_action_uses_shared_service_with_synthetic_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.migration.erebus_capture import menu as capture_menu

    facts = StorageFacts("/dev/x1", "/dev/x", "ext4", EXPECTED_LABEL, EXPECTED_UUID,
                         EXPECTED_MOUNT, "rw", 100, True, True)
    facts_path = tmp_path / "facts.json"; facts_path.write_text(__import__("json").dumps(facts.__dict__))
    values = iter([
        "preview-menu", "/synthetic/repo", "capture-menu", "commit", "tree", RUN_ID,
        "hash", str(tmp_path / "receipt.json"), str(tmp_path / "phase"),
        str(tmp_path / "intake.json"), str(tmp_path / "control"), str(facts_path),
    ])
    captured: list[tuple[CaptureContext, ErebusCaptureRequest]] = []
    monkeypatch.setattr(capture_menu, "_ask", lambda _label: next(values))
    monkeypatch.setattr(capture_menu, "create_preview", lambda context, request: (
        captured.append((context, request)) or SimpleNamespace(ok=True, preview_id=request.preview_id)
    ))
    capture_menu._preview_from_prompts()
    assert len(captured) == 1
    assert captured[0][1].preview_id == "preview-menu"
    assert captured[0][0].storage_resolver().mount_path == EXPECTED_MOUNT


def test_menu_options_are_ready_gated(tmp_path: Path) -> None:
    from mercury.migration.erebus_capture import menu as capture_menu

    assert ("3", "Create approved capture") not in capture_menu.menu_options(tmp_path)

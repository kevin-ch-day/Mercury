"""Phase B execution-lock, failure matrices, and production CLI regression coverage."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mercury.cli import app
from mercury.migration.erebus_capture.context import CaptureContext
from mercury.migration.erebus_capture.contract import REQUIRED, expected_bundle_name, validate_members
from mercury.migration.erebus_capture.full_suite_policy import ExpectedFailure, FullSuiteSummary, evaluate
from mercury.migration.erebus_capture.git_capture import create_complete_bundle
from mercury.migration.erebus_capture.intake_validation import ALLOWED, EXCLUDED
from mercury.migration.erebus_capture.models import ErebusCaptureRequest
from mercury.migration.erebus_capture.package_validation import validate_erebus_capture_for_package
from mercury.migration.erebus_capture.phase3b_validation import BACKUPS, RUN_ID
from mercury.migration.erebus_capture.preview_state import PreviewState, load_state
from mercury.migration.erebus_capture.reconstruction import reconstruct_and_verify
from mercury.migration.erebus_capture.recovery_validation import PATH as RECOVERY_PATH
from mercury.migration.erebus_capture.scanner import scan_capture
from mercury.migration.erebus_capture.service import (
    create_preview,
    execute_capture,
    revalidate_preview_for_execute,
)
from mercury.migration.erebus_capture.storage_preflight import (
    EXPECTED_LABEL,
    EXPECTED_MOUNT,
    EXPECTED_UUID,
    StorageFacts,
)
from mercury.migration.erebus_capture.validation_runner import DeterministicValidationRunner, ValidationResult


def _facts(**overrides: object) -> StorageFacts:
    values = dict(
        partition="/dev/synthetic1",
        parent="/dev/synthetic",
        fstype="ext4",
        label=EXPECTED_LABEL,
        uuid=EXPECTED_UUID,
        mount_path=EXPECTED_MOUNT,
        mount_options="rw",
        free_bytes=100,
        source_host=True,
        writer_enabled=True,
    )
    values.update(overrides)
    return StorageFacts(**values)  # type: ignore[arg-type]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _fail(name: str, *, return_code: int = 1, started: bool = True, completed: bool = True,
          parsed: dict[str, object] | None = None) -> ValidationResult:
    return ValidationResult(
        ("synthetic", name), "/tmp", return_code, started=started, completed=completed, parsed=parsed,
    )


class SyntheticCaptureFixture:
    """Shared READY-preview + synthetic execute environment for Phase B matrices."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        preview_id: str = "preview-matrix",
        capture_id: str = "capture-matrix",
        runner: DeterministicValidationRunner | None = None,
        facts: StorageFacts | None = None,
        allow_synthetic_execution: bool = True,
    ) -> None:
        self.tmp_path = tmp_path
        self.preview_id = preview_id
        self.capture_id = capture_id
        self.facts = facts or _facts()
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        source = self.repo / "src/database/db_query/virustotal_queries/reports"
        source.mkdir(parents=True)
        maintenance = source / "maintenance.py"
        maintenance.write_text("x = 1\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "seed"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "fetch", "origin", "main:refs/remotes/origin/main"],
            check=True, capture_output=True,
        )
        self.commit = _git(self.repo, "rev-parse", "HEAD")
        self.tree = _git(self.repo, "rev-parse", "HEAD^{tree}")
        self.maintenance_sha256 = hashlib.sha256(maintenance.read_bytes()).hexdigest()
        self.control = tmp_path / "control"
        self.receipt = tmp_path / "receipt.json"
        self.receipt.write_text(json.dumps({
            "source_relative_path": RECOVERY_PATH,
            "artifact_sha256": self.maintenance_sha256,
            "repair_commit": self.commit,
            "repair_tree": self.tree,
            "original_ignore_rule": "reports/",
            "repaired_ignore_rule": "/reports/",
            "tracked": True,
        }))
        self.receipt.with_suffix(".json.sha256").write_text(
            f"{hashlib.sha256(self.receipt.read_bytes()).hexdigest()}  receipt.json\n"
        )
        self.phase = tmp_path / "phase"
        (self.phase / "dumps").mkdir(parents=True)
        (self.phase / "restore").mkdir()
        (self.phase / "PHASE3B_REPORT.md").write_text("x")
        (self.phase / "phase3b_summary.json").write_text(json.dumps({"run_id": RUN_ID}))
        (self.phase / "dumps/dump_metadata.json").write_text(json.dumps({"backup_ids": sorted(BACKUPS)}))
        (self.phase / "restore/source_vs_restore_comparison.json").write_text(
            json.dumps({"zero_unexplained_differences": True})
        )
        self.intake = tmp_path / "intake.json"
        self.intake.write_text(json.dumps({
            "schema_version": 1,
            "intake_root_name": "erebus-intake",
            "included_members": sorted(ALLOWED),
            "excluded_members": sorted(EXCLUDED),
            "bypass_allowed": False,
            "mount_guard_required": True,
        }))
        self.intake.with_suffix(".json.sha256").write_text(
            f"{hashlib.sha256(self.intake.read_bytes()).hexdigest()}  intake.json\n"
        )
        self.runner = runner if runner is not None else DeterministicValidationRunner()
        self._facts_box = {"value": self.facts}
        self.context = CaptureContext(
            self.control, self.repo, self.receipt, self.phase, self.intake,
            lambda: self._facts_box["value"],
            allow_synthetic_execution=allow_synthetic_execution,
            validation_runner=self.runner if allow_synthetic_execution else None,
        )
        self.request = ErebusCaptureRequest(
            preview_id, str(self.repo), capture_id, self.commit, self.tree, RUN_ID,
            self.maintenance_sha256, str(self.control),
        )

    def set_facts(self, facts: StorageFacts) -> None:
        self._facts_box["value"] = facts

    def with_runner(self, runner: DeterministicValidationRunner | None) -> CaptureContext:
        return CaptureContext(
            self.control, self.repo, self.receipt, self.phase, self.intake,
            lambda: self._facts_box["value"],
            allow_synthetic_execution=True,
            validation_runner=runner,
        )

    def publish_ready(self, *, preview_id: str | None = None, capture_id: str | None = None,
                      context: CaptureContext | None = None) -> None:
        request = ErebusCaptureRequest(**{
            **self.request.__dict__,
            "preview_id": preview_id or self.preview_id,
            "capture_id": capture_id or self.capture_id,
        })
        preview = create_preview(context or self.context, request)
        assert preview.ok, preview.errors

    def capture_root(self, capture_id: str | None = None) -> Path:
        return self.control / "validation" / "erebus" / (capture_id or self.capture_id)

    def preview_root(self, preview_id: str | None = None) -> Path:
        return self.control / "validation" / "previews" / "erebus" / (preview_id or self.preview_id)

    def leftover_temps(self) -> list[Path]:
        root = self.control / "validation" / "erebus"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.name.startswith(".") and ".tmp-" in path.name)


# ---------------------------------------------------------------------------
# Production lock (unchanged intent)
# ---------------------------------------------------------------------------


def test_default_context_refuses_before_preview_or_capture_paths_exist(tmp_path: Path) -> None:
    context = CaptureContext(tmp_path / "control", tmp_path / "repo", tmp_path / "receipt",
                             tmp_path / "phase", tmp_path / "intake", _facts)
    result = execute_capture(context, "preview-exact")
    assert result.classification == "EXECUTION_NOT_AUTHORIZED"
    assert not (tmp_path / "control" / "validation" / "erebus").exists()


def test_production_cli_has_no_synthetic_execution_bypass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps(_facts().__dict__))
    args = [
        "migration", "capture-erebus-source", "execute", "--preview-id", "preview-exact",
        "--repo", str(repo), "--recovery-receipt", str(tmp_path / "receipt"),
        "--phase3b-root", str(tmp_path / "phase"), "--intake-contract", str(tmp_path / "intake"),
        "--control-root", str(tmp_path / "control"), "--storage-facts", str(facts),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "CAPTURE EXECUTION REFUSED" in result.output
    assert "EXECUTION_NOT_AUTHORIZED" in result.output
    bypass = CliRunner().invoke(app, [*args, "--allow-synthetic-execution"])
    assert bypass.exit_code != 0
    assert not (tmp_path / "control" / "validation" / "erebus").exists()


# ---------------------------------------------------------------------------
# Policy / scanner / contract
# ---------------------------------------------------------------------------


def test_full_suite_policy_core_decisions() -> None:
    approved = (ExpectedFailure("tests/example.py::test_known", "host_output"),)
    assert evaluate(FullSuiteSummary("pytest -q", 0, 10, 10, (), 0)) == (True, "FULL_SUITE_PASS")
    assert evaluate(FullSuiteSummary("pytest -q", 1, 10, 9, approved, 0), approved) == (
        True, "FULL_SUITE_APPROVED_EXCEPTIONS",
    )
    assert evaluate(
        FullSuiteSummary("pytest -q", 1, 10, 9, (ExpectedFailure("tests/other.py::test_new", "host_output"),), 0),
        approved,
    ) == (False, "FULL_SUITE_UNEXPECTED_FAILURES")
    assert evaluate(FullSuiteSummary("pytest -q", 1, 0, 0, (), 0, collection_errors=1)) == (
        False, "FULL_SUITE_STRUCTURAL_FAILURE",
    )
    # Two representative refuse shapes cover return-code inconsistency and structural flags.
    for summary in (
        FullSuiteSummary("pytest -q", 1, 2, 1, (), 0),
        FullSuiteSummary("pytest -q", 1, 2, 1, (), 0, interrupted=True),
    ):
        assert not evaluate(summary)[0]
    approved_one = (ExpectedFailure("tests/a.py::test_a", "host_output"),)
    assert evaluate(FullSuiteSummary("pytest", 0, 1, 1, (), 0), approved_one)[0]
    assert not evaluate(
        FullSuiteSummary("pytest", 1, 1, 0, (ExpectedFailure("tests/a.py::test_a", "different"),), 0),
        approved_one,
    )[0]


def test_validation_runner_result_is_structured_and_refuses_incomplete_execution(tmp_path: Path) -> None:
    result = ValidationResult(("pytest",), str(tmp_path), 1, stderr="failed", parsed={"failed": 1})
    runner = DeterministicValidationRunner({"focused_tests": result})
    recorded = runner.run("focused_tests", cwd=tmp_path, command=("ignored",))
    assert not recorded.accepted
    assert recorded.evidence()["parsed"] == {"failed": 1}


def test_capture_contract_rejects_unexpected_and_historical_members() -> None:
    members = set(REQUIRED) | {expected_bundle_name("abcdef0")}
    assert validate_members(members, "abcdef0") == []
    assert "unexpected member: surprise.txt" in validate_members(members | {"surprise.txt"}, "abcdef0")
    assert "forbidden member: logs/run.txt" in validate_members(members | {"logs/run.txt"}, "abcdef0")


def test_scanner_rejects_and_accepts_representative_content(tmp_path: Path) -> None:
    for name, content in (
        (".env", "API_KEY=abcdefghijklmnop"),
        ("private.pem", "-----BEGIN RSA PRIVATE KEY-----"),
        ("logs/run.txt", "x"),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        assert scan_capture(tmp_path, short_sha="abcdef0")
        path.unlink()
        if path.parent != tmp_path and not any(path.parent.iterdir()):
            path.parent.rmdir()
    for member in set(REQUIRED) | {expected_bundle_name("abcdef0")}:
        path = tmp_path / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("documentation discusses tokens; sha256=" + "a" * 64 + "\n")
    assert scan_capture(tmp_path, short_sha="abcdef0") == []
    (tmp_path / ".env").write_text("")
    assert "forbidden path: .env" not in scan_capture(tmp_path, short_sha="abcdef0")
    target = tmp_path / "target.txt"
    target.write_text("ok")
    link = tmp_path / "artifacts" / "intake_contract" / "link.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    assert any(item.startswith("nonregular member:") for item in scan_capture(tmp_path, short_sha="abcdef0"))


# ---------------------------------------------------------------------------
# Validation / reconstruction / writer / drift
# ---------------------------------------------------------------------------


def test_execute_refuses_injected_validation_and_reconstruction_failures(tmp_path: Path) -> None:
    for step in ("focused_tests", "collection", "compileall", "git_diff_check", "dependency_validation"):
        case = tmp_path / step
        case.mkdir()
        fixture = SyntheticCaptureFixture(case, runner=DeterministicValidationRunner({step: _fail(step)}))
        fixture.publish_ready()
        result = execute_capture(fixture.context, fixture.preview_id)
        assert not result.ok
        assert any(f"VALIDATION_FAILED: {step}" in error for error in result.errors)
        assert load_state(fixture.preview_root()) is PreviewState.REFUSED
        assert not fixture.capture_root().exists()
        assert fixture.leftover_temps() == []
    for kwargs in (
        {"started": False, "completed": True, "return_code": 0},
        {"started": True, "completed": False, "return_code": 0},
    ):
        case = tmp_path / f"full-{kwargs['started']}-{kwargs['completed']}"
        case.mkdir()
        fixture = SyntheticCaptureFixture(
            case,
            runner=DeterministicValidationRunner({"full_suite": _fail("full_suite", **kwargs)}),  # type: ignore[arg-type]
        )
        fixture.publish_ready()
        result = execute_capture(fixture.context, fixture.preview_id)
        assert not result.ok
        assert any("VALIDATION_FAILED: full_suite" in error for error in result.errors)
    policy = tmp_path / "policy"
    policy.mkdir()
    fixture = SyntheticCaptureFixture(
        policy,
        runner=DeterministicValidationRunner({
            "full_suite": _fail(
                "full_suite", return_code=1,
                parsed={
                    "collected_count": 2, "passed_count": 1, "skipped_count": 0,
                    "failures": [{"node_id": "tests/x.py::test_new", "classification": "host_output"}],
                },
            ),
        }),
    )
    fixture.publish_ready()
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("FULL_SUITE_UNEXPECTED_FAILURES" in error for error in result.errors)
    for step in ("reconstruction_import", "reconstruction_focused_tests", "reconstruction_collection"):
        case = tmp_path / step
        case.mkdir()
        fixture = SyntheticCaptureFixture(case, runner=DeterministicValidationRunner({step: _fail(step)}))
        fixture.publish_ready()
        result = execute_capture(fixture.context, fixture.preview_id)
        assert not result.ok
        assert any("RECONSTRUCTION_VALIDATION_FAILED" in error for error in result.errors)
    missing = tmp_path / "missing-runner"
    missing.mkdir()
    fixture = SyntheticCaptureFixture(missing)
    fixture.publish_ready()
    result = execute_capture(fixture.with_runner(None), fixture.preview_id)
    assert not result.ok
    assert any("VALIDATION_RUNNER_REQUIRED" in error for error in result.errors)


def test_reconstruct_and_execute_refuse_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = SyntheticCaptureFixture(tmp_path / "unit")
    bundle = create_complete_bundle(fixture.repo, tmp_path / "repo.bundle", fixture.commit)
    with pytest.raises(subprocess.CalledProcessError):
        reconstruct_and_verify(
            bundle, tmp_path / "bad-commit", expected_commit="0" * 40,
            expected_tree=fixture.tree, maintenance_sha256=fixture.maintenance_sha256,
        )
    tree_result = reconstruct_and_verify(
        bundle, tmp_path / "bad-tree", expected_commit=fixture.commit,
        expected_tree="1" * 40, maintenance_sha256=fixture.maintenance_sha256,
    )
    assert tree_result["tree_match"] is False
    maint = reconstruct_and_verify(
        bundle, tmp_path / "bad-maint", expected_commit=fixture.commit,
        expected_tree=fixture.tree, maintenance_sha256="2" * 64,
    )
    assert maint["maintenance_match"] is False

    from mercury.migration.erebus_capture import writer

    exec_case = SyntheticCaptureFixture(tmp_path / "exec")
    exec_case.publish_ready()

    def fake_reconstruct(bundle_path, destination, **_kwargs):
        destination.mkdir(parents=True, exist_ok=True)
        return {
            "head": "wrong", "tree": "wrong", "clean": True, "maintenance_sha256": "wrong",
            "head_match": False, "tree_match": False, "maintenance_match": False,
        }

    monkeypatch.setattr(writer, "reconstruct_and_verify", fake_reconstruct)
    result = execute_capture(exec_case.context, exec_case.preview_id)
    assert not result.ok
    assert any("RECONSTRUCTION_MISMATCH" in error for error in result.errors)


def test_writer_fault_injection_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.migration.erebus_capture import writer

    # FINAL_CAPTURE_EXISTS race
    race_case = SyntheticCaptureFixture(tmp_path / "race")
    race_case.publish_ready()
    real = writer.write_synthetic_capture

    def race(**kwargs):
        final = kwargs["context"].control_root / "validation" / "erebus" / kwargs["request"].capture_id
        final.mkdir(parents=True)
        return real(**kwargs)

    monkeypatch.setattr(writer, "write_synthetic_capture", race)
    result = execute_capture(race_case.context, race_case.preview_id)
    assert not result.ok and any("FINAL_CAPTURE_EXISTS" in error for error in result.errors)
    assert load_state(race_case.preview_root()) is PreviewState.REFUSED
    monkeypatch.undo()

    for name, patch, needle in (
        ("scan", ("scan_capture", lambda *_a, **_k: ["forbidden path: .env"]), "PROHIBITED_CONTENT"),
        ("manifest", ("verify_manifest", lambda *_a, **_k: False), "MANIFEST_INVALID"),
    ):
        case = SyntheticCaptureFixture(tmp_path / name)
        case.publish_ready()
        monkeypatch.setattr(writer, patch[0], patch[1])
        result = execute_capture(case.context, case.preview_id)
        assert not result.ok and any(needle in error for error in result.errors)
        assert not case.capture_root().exists() and case.leftover_temps() == []
        monkeypatch.undo()

    replace_case = SyntheticCaptureFixture(tmp_path / "replace")
    replace_case.publish_ready()
    real_replace = os.replace

    def boom(src, dst):
        if str(dst).endswith(replace_case.capture_id):
            raise OSError("injected replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(writer.os, "replace", boom)
    result = execute_capture(replace_case.context, replace_case.preview_id)
    assert not result.ok and any("injected replace failure" in error for error in result.errors)
    assert not replace_case.capture_root().exists() and replace_case.leftover_temps() == []


def test_revalidate_drift_reuse_and_lock_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.migration.erebus_capture import writer
    from mercury.migration.erebus_capture.preview_state import begin_execution, invalidate
    from mercury.migration.erebus_capture.service import begin_preview_execution

    def _assert_invalidated(fixture: SyntheticCaptureFixture) -> None:
        result = revalidate_preview_for_execute(fixture.context, fixture.preview_id)
        assert not result.ok
        assert load_state(fixture.preview_root()) is PreviewState.INVALIDATED

    source = SyntheticCaptureFixture(tmp_path / "source")
    source.publish_ready()
    (source.repo / "drift.txt").write_text("untracked\n")
    _assert_invalidated(source)

    storage = SyntheticCaptureFixture(tmp_path / "storage")
    storage.publish_ready()
    storage.set_facts(_facts(uuid="wrong-uuid"))
    _assert_invalidated(storage)

    recovery = SyntheticCaptureFixture(tmp_path / "recovery")
    recovery.publish_ready()
    recovery.receipt.write_text(json.dumps({
        "source_relative_path": RECOVERY_PATH, "artifact_sha256": "wrong",
        "repair_commit": recovery.commit, "repair_tree": recovery.tree,
        "original_ignore_rule": "reports/", "repaired_ignore_rule": "/reports/", "tracked": True,
    }))
    recovery.receipt.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(recovery.receipt.read_bytes()).hexdigest()}  receipt.json\n"
    )
    _assert_invalidated(recovery)

    intake = SyntheticCaptureFixture(tmp_path / "intake")
    intake.publish_ready()
    intake.intake.write_text(json.dumps({
        "schema_version": 2, "intake_root_name": "erebus-intake",
        "included_members": sorted(ALLOWED), "excluded_members": sorted(EXCLUDED),
        "bypass_allowed": False, "mount_guard_required": True,
    }))
    intake.intake.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(intake.intake.read_bytes()).hexdigest()}  intake.json\n"
    )
    _assert_invalidated(intake)

    phase = SyntheticCaptureFixture(tmp_path / "phase")
    phase.publish_ready()
    (phase.phase / "phase3b_summary.json").write_text(json.dumps({"run_id": "wrong"}))
    _assert_invalidated(phase)

    final = SyntheticCaptureFixture(tmp_path / "final")
    final.publish_ready()
    final.capture_root().mkdir(parents=True)
    result = revalidate_preview_for_execute(final.context, final.preview_id)
    assert result.errors == ["FINAL_CAPTURE_EXISTS"]
    assert load_state(final.preview_root()) is PreviewState.INVALIDATED

    reuse = SyntheticCaptureFixture(tmp_path / "reuse")
    reuse.publish_ready()
    assert execute_capture(reuse.context, reuse.preview_id).ok
    assert not execute_capture(reuse.context, reuse.preview_id).ok
    assert load_state(reuse.preview_root()) is PreviewState.CONSUMED

    states = SyntheticCaptureFixture(tmp_path / "states", preview_id="preview-a", capture_id="capture-a")
    states.publish_ready(preview_id="preview-a", capture_id="capture-a")
    invalidate(states.preview_root("preview-a"))
    assert not execute_capture(states.context, "preview-a").ok
    states.publish_ready(preview_id="preview-b", capture_id="capture-b")
    monkeypatch.setattr(writer, "write_synthetic_capture", lambda **_k: (_ for _ in ()).throw(ValueError("injected")))
    assert not execute_capture(states.context, "preview-b").ok
    assert load_state(states.preview_root("preview-b")) is PreviewState.REFUSED
    assert not execute_capture(states.context, "preview-b").ok

    lock_case = SyntheticCaptureFixture(tmp_path / "lock")
    lock_case.publish_ready()
    lock = lock_case.preview_root().parent / f".{lock_case.preview_id}.preview_state.lock"
    lock.write_text("held\n")
    assert begin_execution(lock_case.preview_root()) is False
    assert begin_preview_execution(lock_case.control, lock_case.preview_id).errors == ["PREVIEW_NOT_READY"]
    assert execute_capture(lock_case.context, lock_case.preview_id).errors == ["PREVIEW_NOT_READY"]
    assert load_state(lock_case.preview_root()) is PreviewState.READY


def test_package_validator_refuses_tampered_and_incomplete_captures(tmp_path: Path) -> None:
    assert validate_erebus_capture_for_package(
        tmp_path, capture_id="capture_latest", commit="c", tree="t",
    ) == ["unqualified latest is forbidden"]
    assert validate_erebus_capture_for_package(
        tmp_path, capture_id="candidate", commit="c", tree="t",
    ) == ["verified capture evidence is incomplete"]

    cases = (
        ("malformed", "capture metadata is malformed"),
        ("status", "capture is not verified"),
        ("historical", "capture is not active authority"),
        ("identity", "capture identity mismatch"),
        ("reconstruction", "reconstruction did not pass"),
        ("receipt_class", "manifest receipt is not verified"),
        ("member", "unexpected member"),
        ("manifest", "capture manifest does not verify"),
    )
    for name, needle in cases:
        fixture = SyntheticCaptureFixture(tmp_path / name)
        fixture.publish_ready()
        assert execute_capture(fixture.context, fixture.preview_id).ok
        capture = fixture.capture_root()
        if name == "malformed":
            (capture / "capture_summary.json").write_text("{")
        elif name == "status":
            _patch_json(capture / "capture_summary.json", status="REFUSED")
        elif name == "historical":
            _patch_json(capture / "capture_summary.json", historical_only=True, active_authority=False)
        elif name == "identity":
            _patch_json(capture / "capture_summary.json", commit="0" * 40)
        elif name == "reconstruction":
            _patch_json(capture / "reconstruction/reconstructed_identity.json", head_match=False)
        elif name == "receipt_class":
            _patch_json(capture / "manifest_receipt.json", classification="REFUSED")
        elif name == "member":
            (capture / "surprise.txt").write_text("nope\n")
        else:
            (capture / "CAPTURE_REPORT.md").write_text("tampered\n")
        errors = validate_erebus_capture_for_package(
            fixture.control, capture_id=fixture.capture_id, commit=fixture.commit, tree=fixture.tree,
        )
        assert any(needle in error for error in errors), (name, errors)


def _patch_json(path: Path, **updates: object) -> None:
    data = json.loads(path.read_text())
    data.update(updates)
    path.write_text(json.dumps(data))


def test_menu_ready_gating_and_production_execute_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.migration.erebus_capture import menu as capture_menu
    from mercury.migration.erebus_capture.preview_state import invalidate

    assert capture_menu.menu_options(tmp_path / "missing") == [
        ("1", "Preview capture"), ("2", "Review previews"),
    ]
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    assert capture_menu.menu_options(fixture.control) == [
        ("1", "Preview capture"), ("2", "Review previews"), ("3", "Create approved capture"),
    ]
    assert capture_menu.ready_preview_ids(fixture.control) == [fixture.preview_id]
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(fixture.facts.__dict__))
    values = iter([
        fixture.preview_id, str(fixture.repo), str(fixture.receipt), str(fixture.phase),
        str(fixture.intake), str(facts_path),
    ])
    written: list[str] = []
    monkeypatch.setattr(capture_menu, "_ask", lambda _label: next(values))
    monkeypatch.setattr(capture_menu.output, "write", written.append)
    capture_menu._execute_from_prompts(fixture.control)
    assert any("CAPTURE EXECUTION REFUSED" in line for line in written)
    assert any("EXECUTION_NOT_AUTHORIZED" in line for line in written)
    invalidate(fixture.preview_root())
    assert capture_menu.menu_options(fixture.control) == [
        ("1", "Preview capture"), ("2", "Review previews"),
    ]

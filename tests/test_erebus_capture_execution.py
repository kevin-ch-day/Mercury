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
# Full-suite policy / scanner / contract (existing)
# ---------------------------------------------------------------------------


def test_full_suite_policy_requires_exact_failure_identity_and_classification() -> None:
    approved = (ExpectedFailure("tests/example.py::test_known", "host_output"),)
    clean = FullSuiteSummary("pytest -q", 0, 10, 10, (), 0)
    assert evaluate(clean) == (True, "FULL_SUITE_PASS")
    exact = FullSuiteSummary("pytest -q", 1, 10, 9, approved, 0)
    assert evaluate(exact, approved) == (True, "FULL_SUITE_APPROVED_EXCEPTIONS")
    changed = FullSuiteSummary("pytest -q", 1, 10, 9, (ExpectedFailure("tests/other.py::test_new", "host_output"),), 0)
    assert evaluate(changed, approved) == (False, "FULL_SUITE_UNEXPECTED_FAILURES")
    assert evaluate(FullSuiteSummary("pytest -q", 1, 0, 0, (), 0, collection_errors=1)) == (False, "FULL_SUITE_STRUCTURAL_FAILURE")


@pytest.mark.parametrize("summary", [
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0),
    FullSuiteSummary("pytest -q", 0, 2, 1, (ExpectedFailure("x", "wrong"),), 0),
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0, interrupted=True),
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0, focused_failures=1),
    FullSuiteSummary("pytest -q", 1, 2, 1, (), 0, dependency_valid=False),
])
def test_full_suite_policy_refuses_inconsistent_or_incomplete_results(summary: FullSuiteSummary) -> None:
    assert not evaluate(summary)[0]


def test_full_suite_policy_refuses_missing_or_changed_approved_failure() -> None:
    approved = (ExpectedFailure("tests/a.py::test_a", "host_output"),)
    missing = FullSuiteSummary("pytest", 0, 1, 1, (), 0)
    assert evaluate(missing, approved)[0]
    changed = FullSuiteSummary("pytest", 1, 1, 0, (ExpectedFailure("tests/a.py::test_a", "different"),), 0)
    assert not evaluate(changed, approved)[0]


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


@pytest.mark.parametrize("name,content", [
    (".env", "API_KEY=abcdefghijklmnop"), ("private.pem", "-----BEGIN RSA PRIVATE KEY-----"),
    ("private.txt", "-----BEGIN OPENSSH PRIVATE KEY-----"), ("private.txt", "-----BEGIN EC PRIVATE KEY-----"),
    ("data.db", "binary"), ("logs/run.txt", "x"), ("output/report.txt", "x"),
    ("ScytaleDroid/source.py", "x"), ("notes.txt", "token=abcdefghijklmnop"),
])
def test_scanner_rejects_governed_forbidden_content(tmp_path: Path, name: str, content: str) -> None:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    assert scan_capture(tmp_path, short_sha="abcdef0")


def test_scanner_accepts_benign_hashes_and_docs_when_members_are_valid(tmp_path: Path) -> None:
    for member in set(REQUIRED) | {expected_bundle_name("abcdef0")}:
        path = tmp_path / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("documentation discusses tokens; sha256=" + "a" * 64 + "\n")
    assert scan_capture(tmp_path, short_sha="abcdef0") == []


def test_scanner_accepts_empty_env_template_and_refuses_symlink(tmp_path: Path) -> None:
    for member in set(REQUIRED) | {expected_bundle_name("abcdef0")}:
        path = tmp_path / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n")
    (tmp_path / ".env").write_text("")
    assert "forbidden path: .env" not in scan_capture(tmp_path, short_sha="abcdef0")
    target = tmp_path / "target.txt"
    target.write_text("ok")
    link = tmp_path / "artifacts" / "intake_contract" / "link.txt"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    assert any(item.startswith("nonregular member:") for item in scan_capture(tmp_path, short_sha="abcdef0"))


# ---------------------------------------------------------------------------
# 1. Validation / reconstruction failure matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", [
    "focused_tests", "collection", "compileall", "git_diff_check", "dependency_validation",
])
def test_execute_refuses_injected_validation_step_failure(tmp_path: Path, step: str) -> None:
    fixture = SyntheticCaptureFixture(
        tmp_path,
        runner=DeterministicValidationRunner({step: _fail(step)}),
    )
    fixture.publish_ready()
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any(f"VALIDATION_FAILED: {step}" in error for error in result.errors)
    assert load_state(fixture.preview_root()) is PreviewState.REFUSED
    assert not fixture.capture_root().exists()
    assert fixture.leftover_temps() == []


@pytest.mark.parametrize("kwargs", [
    {"started": False, "completed": True, "return_code": 0},
    {"started": True, "completed": False, "return_code": 0},
])
def test_execute_refuses_incomplete_full_suite(tmp_path: Path, kwargs: dict[str, object]) -> None:
    fixture = SyntheticCaptureFixture(
        tmp_path,
        runner=DeterministicValidationRunner({"full_suite": _fail("full_suite", **kwargs)}),  # type: ignore[arg-type]
    )
    fixture.publish_ready()
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("VALIDATION_FAILED: full_suite" in error for error in result.errors)
    assert fixture.leftover_temps() == []


def test_execute_refuses_full_suite_policy_rejection(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(
        tmp_path,
        runner=DeterministicValidationRunner({
            "full_suite": _fail(
                "full_suite",
                return_code=1,
                parsed={
                    "collected_count": 2,
                    "passed_count": 1,
                    "skipped_count": 0,
                    "failures": [{"node_id": "tests/x.py::test_new", "classification": "host_output"}],
                },
            ),
        }),
    )
    fixture.publish_ready()
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("FULL_SUITE_UNEXPECTED_FAILURES" in error for error in result.errors)
    assert fixture.leftover_temps() == []


@pytest.mark.parametrize("step", [
    "reconstruction_import", "reconstruction_focused_tests", "reconstruction_collection",
])
def test_execute_refuses_reconstruction_validation_failure(tmp_path: Path, step: str) -> None:
    fixture = SyntheticCaptureFixture(
        tmp_path,
        runner=DeterministicValidationRunner({step: _fail(step)}),
    )
    fixture.publish_ready()
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("RECONSTRUCTION_VALIDATION_FAILED" in error for error in result.errors)
    assert fixture.leftover_temps() == []


def test_execute_refuses_missing_validation_runner(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    context = fixture.with_runner(None)
    result = execute_capture(context, fixture.preview_id)
    assert not result.ok
    assert any("VALIDATION_RUNNER_REQUIRED" in error for error in result.errors)
    assert fixture.leftover_temps() == []


@pytest.mark.parametrize("fault", ["tree", "maintenance", "commit"])
def test_reconstruct_and_verify_refuses_identity_mismatch(tmp_path: Path, fault: str) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    bundle = create_complete_bundle(fixture.repo, tmp_path / "repo.bundle", fixture.commit)
    if fault == "commit":
        with pytest.raises(subprocess.CalledProcessError):
            reconstruct_and_verify(
                bundle, tmp_path / "reconstructed", expected_commit="0" * 40,
                expected_tree=fixture.tree, maintenance_sha256=fixture.maintenance_sha256,
            )
        return
    tree = "1" * 40 if fault == "tree" else fixture.tree
    digest = "2" * 64 if fault == "maintenance" else fixture.maintenance_sha256
    result = reconstruct_and_verify(
        bundle, tmp_path / "reconstructed", expected_commit=fixture.commit,
        expected_tree=tree, maintenance_sha256=digest,
    )
    if fault == "tree":
        assert result["tree_match"] is False
    else:
        assert result["maintenance_match"] is False


def test_execute_refuses_reconstruction_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.migration.erebus_capture import writer

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()

    def fake_reconstruct(bundle, destination, **_kwargs):
        destination.mkdir(parents=True, exist_ok=True)
        return {
            "head": "wrong", "tree": "wrong", "clean": True, "maintenance_sha256": "wrong",
            "head_match": False, "tree_match": False, "maintenance_match": False,
        }

    monkeypatch.setattr(writer, "reconstruct_and_verify", fake_reconstruct)
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("RECONSTRUCTION_MISMATCH" in error for error in result.errors)
    assert fixture.leftover_temps() == []


# ---------------------------------------------------------------------------
# 2. Atomic-writer fault injection
# ---------------------------------------------------------------------------


def test_writer_refuses_when_final_capture_appears_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.migration.erebus_capture import writer

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    real = writer.write_synthetic_capture

    def race(**kwargs):
        final = kwargs["context"].control_root / "validation" / "erebus" / kwargs["request"].capture_id
        final.mkdir(parents=True)
        return real(**kwargs)

    monkeypatch.setattr(writer, "write_synthetic_capture", race)
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("FINAL_CAPTURE_EXISTS" in error for error in result.errors)
    assert load_state(fixture.preview_root()) is PreviewState.REFUSED
    assert fixture.leftover_temps() == []


def test_writer_refuses_prohibited_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.migration.erebus_capture import writer

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    monkeypatch.setattr(writer, "scan_capture", lambda *_args, **_kwargs: ["forbidden path: .env"])
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("PROHIBITED_CONTENT" in error for error in result.errors)
    assert not fixture.capture_root().exists()
    assert fixture.leftover_temps() == []


def test_writer_refuses_invalid_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.migration.erebus_capture import writer

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    monkeypatch.setattr(writer, "verify_manifest", lambda *_args, **_kwargs: False)
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("MANIFEST_INVALID" in error for error in result.errors)
    assert not fixture.capture_root().exists()
    assert fixture.leftover_temps() == []


def test_writer_cleans_temp_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.migration.erebus_capture import writer

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    real_replace = os.replace

    def boom(src, dst):
        if str(dst).endswith(fixture.capture_id):
            raise OSError("injected replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(writer.os, "replace", boom)
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert any("injected replace failure" in error for error in result.errors)
    assert not fixture.capture_root().exists()
    assert fixture.leftover_temps() == []


# ---------------------------------------------------------------------------
# 3. Execute-time drift / reuse / concurrency
# ---------------------------------------------------------------------------


def test_revalidate_invalidates_on_source_drift(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    (fixture.repo / "drift.txt").write_text("untracked\n")
    result = revalidate_preview_for_execute(fixture.context, fixture.preview_id)
    assert not result.ok
    assert load_state(fixture.preview_root()) is PreviewState.INVALIDATED


def test_revalidate_invalidates_on_storage_drift(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    fixture.set_facts(_facts(uuid="wrong-uuid"))
    result = revalidate_preview_for_execute(fixture.context, fixture.preview_id)
    assert not result.ok
    assert load_state(fixture.preview_root()) is PreviewState.INVALIDATED


def test_revalidate_invalidates_on_recovery_drift(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    fixture.receipt.write_text(json.dumps({
        "source_relative_path": RECOVERY_PATH,
        "artifact_sha256": "wrong",
        "repair_commit": fixture.commit,
        "repair_tree": fixture.tree,
        "original_ignore_rule": "reports/",
        "repaired_ignore_rule": "/reports/",
        "tracked": True,
    }))
    fixture.receipt.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(fixture.receipt.read_bytes()).hexdigest()}  receipt.json\n"
    )
    result = revalidate_preview_for_execute(fixture.context, fixture.preview_id)
    assert not result.ok
    assert load_state(fixture.preview_root()) is PreviewState.INVALIDATED


def test_revalidate_invalidates_on_intake_drift(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    fixture.intake.write_text(json.dumps({
        "schema_version": 2,
        "intake_root_name": "erebus-intake",
        "included_members": sorted(ALLOWED),
        "excluded_members": sorted(EXCLUDED),
        "bypass_allowed": False,
        "mount_guard_required": True,
    }))
    fixture.intake.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(fixture.intake.read_bytes()).hexdigest()}  intake.json\n"
    )
    result = revalidate_preview_for_execute(fixture.context, fixture.preview_id)
    assert not result.ok
    assert load_state(fixture.preview_root()) is PreviewState.INVALIDATED


def test_revalidate_invalidates_on_phase3b_drift(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    (fixture.phase / "phase3b_summary.json").write_text(json.dumps({"run_id": "wrong"}))
    result = revalidate_preview_for_execute(fixture.context, fixture.preview_id)
    assert not result.ok
    assert load_state(fixture.preview_root()) is PreviewState.INVALIDATED


def test_revalidate_invalidates_when_final_capture_exists(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    fixture.capture_root().mkdir(parents=True)
    result = revalidate_preview_for_execute(fixture.context, fixture.preview_id)
    assert not result.ok
    assert result.errors == ["FINAL_CAPTURE_EXISTS"]
    assert load_state(fixture.preview_root()) is PreviewState.INVALIDATED


def test_second_execute_after_success_is_refused(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    first = execute_capture(fixture.context, fixture.preview_id)
    assert first.ok, first.errors
    second = execute_capture(fixture.context, fixture.preview_id)
    assert not second.ok
    assert load_state(fixture.preview_root()) is PreviewState.CONSUMED


def test_execute_refuses_invalidated_and_refused_previews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.migration.erebus_capture import writer
    from mercury.migration.erebus_capture.preview_state import invalidate

    fixture = SyntheticCaptureFixture(tmp_path, preview_id="preview-a", capture_id="capture-a")
    fixture.publish_ready(preview_id="preview-a", capture_id="capture-a")
    invalidate(fixture.preview_root("preview-a"))
    assert not execute_capture(fixture.context, "preview-a").ok
    assert not fixture.capture_root("capture-a").exists()

    fixture.publish_ready(preview_id="preview-b", capture_id="capture-b")
    monkeypatch.setattr(
        writer, "write_synthetic_capture",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("injected")),
    )
    assert not execute_capture(fixture.context, "preview-b").ok
    assert load_state(fixture.preview_root("preview-b")) is PreviewState.REFUSED
    assert not execute_capture(fixture.context, "preview-b").ok
    assert not fixture.capture_root("capture-b").exists()


def test_begin_execution_fails_closed_when_lock_held(tmp_path: Path) -> None:
    from mercury.migration.erebus_capture.preview_state import begin_execution
    from mercury.migration.erebus_capture.service import begin_preview_execution

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    lock = fixture.preview_root().parent / f".{fixture.preview_id}.preview_state.lock"
    lock.write_text("held\n")
    assert begin_execution(fixture.preview_root()) is False
    result = begin_preview_execution(fixture.control, fixture.preview_id)
    assert not result.ok
    assert result.errors == ["PREVIEW_NOT_READY"]
    assert load_state(fixture.preview_root()) is PreviewState.READY


def test_execute_refuses_when_preview_lock_is_held(tmp_path: Path) -> None:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    lock = fixture.preview_root().parent / f".{fixture.preview_id}.preview_state.lock"
    lock.write_text("held\n")
    result = execute_capture(fixture.context, fixture.preview_id)
    assert not result.ok
    assert result.errors == ["PREVIEW_NOT_READY"]
    assert not fixture.capture_root().exists()
    assert load_state(fixture.preview_root()) is PreviewState.READY


# ---------------------------------------------------------------------------
# 4. Package / manifest tamper coverage
# ---------------------------------------------------------------------------


def _golden_capture(tmp_path: Path) -> tuple[SyntheticCaptureFixture, Path]:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    result = execute_capture(fixture.context, fixture.preview_id)
    assert result.ok, result.errors
    capture = fixture.capture_root()
    assert validate_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id, commit=fixture.commit, tree=fixture.tree,
    ) == []
    return fixture, capture


def test_package_validator_refuses_latest_capture_id(tmp_path: Path) -> None:
    assert validate_erebus_capture_for_package(
        tmp_path, capture_id="capture_latest", commit="c", tree="t",
    ) == ["unqualified latest is forbidden"]


def test_package_validator_refuses_incomplete_capture(tmp_path: Path) -> None:
    assert validate_erebus_capture_for_package(
        tmp_path, capture_id="candidate", commit="c", tree="t",
    ) == ["verified capture evidence is incomplete"]


@pytest.mark.parametrize("mutator,needle", [
    ("malformed", "capture metadata is malformed"),
    ("status", "capture is not verified"),
    ("historical", "capture is not active authority"),
    ("identity", "capture identity mismatch"),
    ("reconstruction", "reconstruction did not pass"),
    ("receipt_class", "manifest receipt is not verified"),
    ("receipt_identity", "manifest identity mismatch"),
    ("recovery", "maintenance recovery mismatch"),
    ("phase", "Phase 3B backup identity mismatch"),
    ("supersession", "supersession metadata mismatch"),
    ("member", "unexpected member"),
    ("manifest", "capture manifest does not verify"),
])
def test_package_validator_refuses_tampered_capture(
    tmp_path: Path, mutator: str, needle: str,
) -> None:
    fixture, capture = _golden_capture(tmp_path)
    if mutator == "malformed":
        (capture / "capture_summary.json").write_text("{")
    elif mutator == "status":
        data = json.loads((capture / "capture_summary.json").read_text())
        data["status"] = "REFUSED"
        (capture / "capture_summary.json").write_text(json.dumps(data))
    elif mutator == "historical":
        data = json.loads((capture / "capture_summary.json").read_text())
        data["historical_only"] = True
        data["active_authority"] = False
        (capture / "capture_summary.json").write_text(json.dumps(data))
    elif mutator == "identity":
        data = json.loads((capture / "capture_summary.json").read_text())
        data["commit"] = "0" * 40
        (capture / "capture_summary.json").write_text(json.dumps(data))
    elif mutator == "reconstruction":
        data = json.loads((capture / "reconstruction/reconstructed_identity.json").read_text())
        data["head_match"] = False
        (capture / "reconstruction/reconstructed_identity.json").write_text(json.dumps(data))
    elif mutator == "receipt_class":
        data = json.loads((capture / "manifest_receipt.json").read_text())
        data["classification"] = "REFUSED"
        (capture / "manifest_receipt.json").write_text(json.dumps(data))
    elif mutator == "receipt_identity":
        data = json.loads((capture / "manifest_receipt.json").read_text())
        data["tree"] = "0" * 40
        (capture / "manifest_receipt.json").write_text(json.dumps(data))
    elif mutator == "recovery":
        data = json.loads((capture / "artifacts/source_recovery/maintenance_source_recovery.json").read_text())
        data["artifact_sha256"] = "0" * 64
        (capture / "artifacts/source_recovery/maintenance_source_recovery.json").write_text(json.dumps(data))
    elif mutator == "phase":
        data = json.loads((capture / "phase3b_linkage.json").read_text())
        data["backup_ids"] = []
        (capture / "phase3b_linkage.json").write_text(json.dumps(data))
    elif mutator == "supersession":
        data = json.loads((capture / "supersession.json").read_text())
        data["supersedes"] = "wrong"
        (capture / "supersession.json").write_text(json.dumps(data))
    elif mutator == "member":
        (capture / "surprise.txt").write_text("nope\n")
    else:
        (capture / "CAPTURE_REPORT.md").write_text("tampered\n")
    errors = validate_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id, commit=fixture.commit, tree=fixture.tree,
    )
    assert any(needle in error for error in errors), errors


# ---------------------------------------------------------------------------
# 5. READY-only menu execute visibility
# ---------------------------------------------------------------------------


def test_menu_options_omit_execute_without_ready_preview(tmp_path: Path) -> None:
    from mercury.migration.erebus_capture import menu as capture_menu

    assert capture_menu.menu_options(tmp_path / "missing") == [
        ("1", "Preview capture"), ("2", "Review previews"),
    ]
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    from mercury.migration.erebus_capture.preview_state import invalidate
    invalidate(fixture.preview_root())
    assert capture_menu.menu_options(fixture.control) == [
        ("1", "Preview capture"), ("2", "Review previews"),
    ]


def test_menu_options_include_execute_when_ready_preview_exists(tmp_path: Path) -> None:
    from mercury.migration.erebus_capture import menu as capture_menu

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    assert capture_menu.menu_options(fixture.control) == [
        ("1", "Preview capture"), ("2", "Review previews"), ("3", "Create approved capture"),
    ]
    assert capture_menu.ready_preview_ids(fixture.control) == [fixture.preview_id]


def test_menu_execute_stays_production_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.migration.erebus_capture import menu as capture_menu

    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
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
    assert not fixture.capture_root().exists()

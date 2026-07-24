"""Phase C: destination package authority from governed Erebus captures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.core.storage_roles import CONTROL_DIRNAME
from mercury.migration.destination_package import preview_destination_package
from mercury.migration.destination_package_create import (
    CREATE_CONFIRMATION,
    create_destination_package,
    packages_root,
)
from mercury.migration.erebus_capture.package_validation import (
    assess_erebus_capture_for_package,
    read_erebus_capture_identity,
)
from mercury.migration.erebus_capture.service import execute_capture
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance
from mercury.storage.retention import RetentionPolicy

from test_destination_package_create import (
    BACKUP_IDS,
    EREBUS_CAPTURE,
    EREBUS_COMMIT,
    MERCURY_CAPTURE,
    MERCURY_COMMIT,
    PREVIEW_ID,
    RUN_ID,
    _member,
    _seal_preview,
    _write_docs,
)
from test_erebus_capture_execution import SyntheticCaptureFixture


@pytest.fixture
def host_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    save_host_maintenance(HostMaintenanceState(), path=path)
    return path


@pytest.fixture
def mount_ok(monkeypatch: pytest.MonkeyPatch):
    import mercury.migration.destination_package_create as create_mod

    class _Ok:
        ok = True
        blocker = None
        code = "ok"

    monkeypatch.setattr(create_mod, "validate_storage_mount", lambda **kwargs: _Ok())
    monkeypatch.setattr(Path, "is_mount", lambda self: True)


def _write_summary(control: Path, capture_id: str, payload: dict) -> None:
    capture = control / "validation" / "erebus" / capture_id
    capture.mkdir(parents=True, exist_ok=True)
    (capture / "capture_summary.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _golden(tmp_path: Path) -> SyntheticCaptureFixture:
    fixture = SyntheticCaptureFixture(tmp_path)
    fixture.publish_ready()
    result = execute_capture(fixture.context, fixture.preview_id)
    assert result.ok, result.errors
    return fixture


def _preview_policy(*capture_ids: str) -> RetentionPolicy:
    return RetentionPolicy(
        protected_capture_ids=capture_ids,
        protected_run_ids=(RUN_ID,),
        protected_backup_ids=(),
    )


def _seed_phase(mount: Path) -> None:
    phase = mount / CONTROL_DIRNAME / "phase3b" / RUN_ID
    phase.mkdir(parents=True)
    (phase / "PHASE3B_REPORT.md").write_text("x\n")


def _seal_with_capture(mount: Path, capture_id: str) -> None:
    evidence = mount / "evidence" / "phase.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("ok\n")
    members = [_member(evidence, "phase3b_evidence", "phase.txt")]
    _write_docs(mount)
    _seal_preview(mount, members)
    preview_path = (
        mount / CONTROL_DIRNAME / "destination" / RUN_ID / "previews" / PREVIEW_ID / "preview.json"
    )
    data = json.loads(preview_path.read_text())
    data["included_capture_ids"] = [MERCURY_CAPTURE, capture_id]
    from mercury.migration.destination_package_seal import compute_preview_sha256

    data["preview_sha256"] = compute_preview_sha256(data)
    preview_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    (preview_path.parent / "PREVIEW.sha256").write_text(data["preview_sha256"] + "\n")


def test_identity_shapes_and_refusals(tmp_path: Path) -> None:
    control = tmp_path / "control"
    _write_summary(control, "canonical", {"commit": "a" * 40, "tree": "b" * 40})
    assert read_erebus_capture_identity(control, "canonical") == ("a" * 40, "b" * 40)
    _write_summary(control, "legacy", {"repository": {"commit": "c" * 40, "tree": "d" * 40}})
    assert read_erebus_capture_identity(control, "legacy") == ("c" * 40, "d" * 40)
    _write_summary(control, "dual", {
        "commit": "a" * 40, "tree": "b" * 40,
        "repository": {"commit": "a" * 40, "tree": "b" * 40},
    })
    assert read_erebus_capture_identity(control, "dual") == ("a" * 40, "b" * 40)
    _write_summary(control, "conflict", {
        "commit": "a" * 40, "tree": "b" * 40,
        "repository": {"commit": "c" * 40, "tree": "b" * 40},
    })
    with pytest.raises(ValueError, match="conflicting capture identity"):
        read_erebus_capture_identity(control, "conflict")
    for idx, payload in enumerate((
        {"tree": "b" * 40},
        {"commit": "a" * 40},
        {"commit": 123, "tree": "b" * 40},
        {"commit": "a" * 40, "tree": ["bad"]},
    )):
        _write_summary(control, f"bad-{idx}", payload)
        with pytest.raises(ValueError):
            read_erebus_capture_identity(control, f"bad-{idx}")


def test_assess_classifications(tmp_path: Path) -> None:
    assert assess_erebus_capture_for_package(tmp_path, capture_id="capture_latest").classification == "REFUSED"
    assert assess_erebus_capture_for_package(tmp_path, capture_id="missing").classification == "MISSING"
    fixture = _golden(tmp_path / "auth")
    ok = assess_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id,
        expected_commit=fixture.commit, expected_tree=fixture.tree,
    )
    assert ok.classification == "PACKAGE_AUTHORITY"
    assert assess_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id, expected_commit="0" * 40,
    ).classification == "REFUSED"
    assert assess_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id, expected_tree="1" * 40,
    ).classification == "REFUSED"
    _write_summary(tmp_path / "hist", "old", {"repository": {"commit": "a" * 40, "tree": "b" * 40}})
    assert assess_erebus_capture_for_package(tmp_path / "hist", capture_id="old").classification == "HISTORICAL_REFERENCE"


def test_preview_authority_gates(tmp_path: Path) -> None:
    fixture = _golden(tmp_path / "synth")
    mount = tmp_path / "mnt"
    mount.mkdir()
    dest = mount / CONTROL_DIRNAME / "validation" / "erebus" / fixture.capture_id
    dest.parent.mkdir(parents=True)
    fixture.capture_root().rename(dest)
    _seed_phase(mount)
    report = preview_destination_package(
        mount, run_id=RUN_ID, policy=_preview_policy(fixture.capture_id),
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
    )
    assert fixture.capture_id in report.included_capture_ids
    assert any(item.startswith("erebus_package_authority_commit=") for item in report.included_git_commits)

    _write_summary(mount / CONTROL_DIRNAME, "bad-verified", {
        "status": "CAPTURE_VERIFIED", "active_authority": True,
        "commit": "a" * 40, "tree": "b" * 40,
    })
    bad = preview_destination_package(
        mount, run_id=RUN_ID, policy=_preview_policy("bad-verified"),
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
    )
    assert any("Erebus capture bad-verified" in error for error in bad.errors)

    _write_summary(mount / CONTROL_DIRNAME, "historical", {
        "repository": {"commit": "a" * 40, "tree": "b" * 40},
    })
    historical = preview_destination_package(
        mount, run_id=RUN_ID, policy=_preview_policy("historical"),
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
    )
    assert "erebus_historical_reference=historical" in historical.included_git_commits

    missing = preview_destination_package(
        mount, run_id=RUN_ID, policy=_preview_policy("absent-capture"),
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
    )
    assert any("required capture missing: absent-capture" in error for error in missing.errors)
    latest = preview_destination_package(
        mount, run_id=RUN_ID, policy=_preview_policy("capture_latest"),
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
    )
    assert any("unqualified latest" in error for error in latest.errors)


def test_create_authority_gates(tmp_path: Path, host_state: Path, mount_ok: None) -> None:
    fixture = _golden(tmp_path / "synth")
    mount = tmp_path / "ok"
    mount.mkdir()
    dest = mount / CONTROL_DIRNAME / "validation" / "erebus" / fixture.capture_id
    dest.parent.mkdir(parents=True)
    fixture.capture_root().rename(dest)
    _seal_with_capture(mount, fixture.capture_id)
    ok = create_destination_package(
        mount, preview_id=PREVIEW_ID, run_id=RUN_ID, confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=fixture.commit, erebus_capture_id=fixture.capture_id,
        expected_backup_ids=BACKUP_IDS, verify_git_head=False,
        package_id="destination_rehearsal_authority_ok",
    )
    assert ok.ok, ok.errors

    hist = tmp_path / "hist"
    hist.mkdir()
    _write_summary(hist / CONTROL_DIRNAME, EREBUS_CAPTURE, {
        "repository": {"commit": EREBUS_COMMIT, "tree": "e" * 40},
    })
    _seal_with_capture(hist, EREBUS_CAPTURE)
    historical = create_destination_package(
        hist, preview_id=PREVIEW_ID, run_id=RUN_ID, confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=EREBUS_COMMIT, erebus_capture_id=EREBUS_CAPTURE,
        expected_backup_ids=BACKUP_IDS, verify_git_head=False,
        package_id="destination_rehearsal_historical_refuse",
    )
    assert not historical.ok
    assert any("historical and cannot authorize" in error for error in historical.errors)

    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    _seal_with_capture(missing_root, EREBUS_CAPTURE)
    missing = create_destination_package(
        missing_root, preview_id=PREVIEW_ID, run_id=RUN_ID, confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=EREBUS_COMMIT, erebus_capture_id=EREBUS_CAPTURE,
        expected_backup_ids=BACKUP_IDS, verify_git_head=False,
        package_id="destination_rehearsal_missing_refuse",
        allow_synthetic_missing_capture_fixture=False,
    )
    assert not missing.ok and any("required capture missing" in error for error in missing.errors)

    allowed = create_destination_package(
        missing_root, preview_id=PREVIEW_ID, run_id=RUN_ID, confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=EREBUS_COMMIT, erebus_capture_id=EREBUS_CAPTURE,
        expected_backup_ids=BACKUP_IDS, verify_git_head=False,
        package_id="destination_rehearsal_missing_fixture",
        allow_synthetic_missing_capture_fixture=True,
    )
    assert allowed.ok, allowed.errors
    assert any("synthetic missing capture fixture allowed" in warning for warning in allowed.warnings)

    # Create-path refuse for a few authority faults (validator unit coverage lives in execution suite).
    for mutator, needle in (
        ("manifest", "Erebus capture"),
        ("authority", "historical and cannot authorize"),
        ("phase", "Phase 3B backup identity mismatch"),
    ):
        case = tmp_path / mutator
        golden = _golden(case / "synth")
        mnt = case / "mnt"
        mnt.mkdir()
        capture = mnt / CONTROL_DIRNAME / "validation" / "erebus" / golden.capture_id
        capture.parent.mkdir(parents=True)
        golden.capture_root().rename(capture)
        if mutator == "manifest":
            (capture / "CAPTURE_REPORT.md").write_text("tampered\n")
        elif mutator == "authority":
            data = json.loads((capture / "capture_summary.json").read_text())
            data["active_authority"] = False
            data["historical_only"] = True
            (capture / "capture_summary.json").write_text(json.dumps(data))
        else:
            data = json.loads((capture / "phase3b_linkage.json").read_text())
            data["backup_ids"] = []
            (capture / "phase3b_linkage.json").write_text(json.dumps(data))
        _seal_with_capture(mnt, golden.capture_id)
        result = create_destination_package(
            mnt, preview_id=PREVIEW_ID, run_id=RUN_ID, confirm=CREATE_CONFIRMATION,
            mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
            erebus_commit=golden.commit, erebus_capture_id=golden.capture_id,
            expected_backup_ids=BACKUP_IDS, verify_git_head=False,
            package_id=f"destination_rehearsal_{mutator}_refuse",
        )
        assert not result.ok
        assert any(needle in error for error in result.errors), result.errors
        assert not (packages_root(mnt) / f"destination_rehearsal_{mutator}_refuse").exists()

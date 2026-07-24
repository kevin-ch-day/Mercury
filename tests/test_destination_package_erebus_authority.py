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

from test_erebus_capture_execution import SyntheticCaptureFixture
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


def _write_summary(control: Path, capture_id: str, payload: dict) -> Path:
    capture = control / "validation" / "erebus" / capture_id
    capture.mkdir(parents=True, exist_ok=True)
    path = capture / "capture_summary.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return capture


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


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_identity_canonical_only(tmp_path: Path) -> None:
    control = tmp_path / "control"
    _write_summary(control, "cap", {"commit": "a" * 40, "tree": "b" * 40})
    assert read_erebus_capture_identity(control, "cap") == ("a" * 40, "b" * 40)


def test_identity_legacy_only(tmp_path: Path) -> None:
    control = tmp_path / "control"
    _write_summary(control, "cap", {"repository": {"commit": "c" * 40, "tree": "d" * 40}})
    assert read_erebus_capture_identity(control, "cap") == ("c" * 40, "d" * 40)


def test_identity_matching_dual(tmp_path: Path) -> None:
    control = tmp_path / "control"
    _write_summary(control, "cap", {
        "commit": "a" * 40, "tree": "b" * 40,
        "repository": {"commit": "a" * 40, "tree": "b" * 40},
    })
    assert read_erebus_capture_identity(control, "cap") == ("a" * 40, "b" * 40)


def test_identity_conflicting_dual_refuses(tmp_path: Path) -> None:
    control = tmp_path / "control"
    _write_summary(control, "cap", {
        "commit": "a" * 40, "tree": "b" * 40,
        "repository": {"commit": "c" * 40, "tree": "b" * 40},
    })
    with pytest.raises(ValueError, match="conflicting capture identity"):
        read_erebus_capture_identity(control, "cap")


@pytest.mark.parametrize("payload", [
    {"tree": "b" * 40},
    {"commit": "a" * 40},
    {"commit": 123, "tree": "b" * 40},
    {"commit": "a" * 40, "tree": ["bad"]},
])
def test_identity_incomplete_or_malformed_refuses(tmp_path: Path, payload: dict) -> None:
    control = tmp_path / "control"
    _write_summary(control, "cap", payload)
    with pytest.raises(ValueError):
        read_erebus_capture_identity(control, "cap")


def test_assess_wrong_expected_commit_and_tree(tmp_path: Path) -> None:
    fixture = _golden(tmp_path)
    wrong_commit = assess_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id, expected_commit="0" * 40,
    )
    assert wrong_commit.classification == "REFUSED"
    wrong_tree = assess_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id, expected_tree="1" * 40,
    )
    assert wrong_tree.classification == "REFUSED"


def test_assess_latest_and_missing(tmp_path: Path) -> None:
    assert assess_erebus_capture_for_package(tmp_path, capture_id="capture_latest").classification == "REFUSED"
    assert assess_erebus_capture_for_package(tmp_path, capture_id="missing").classification == "MISSING"


def test_assess_package_authority_and_historical(tmp_path: Path) -> None:
    fixture = _golden(tmp_path)
    ok = assess_erebus_capture_for_package(
        fixture.control, capture_id=fixture.capture_id, expected_commit=fixture.commit, expected_tree=fixture.tree,
    )
    assert ok.classification == "PACKAGE_AUTHORITY"
    historical = tmp_path / "hist"
    _write_summary(historical, "old", {"repository": {"commit": "a" * 40, "tree": "b" * 40}})
    assessed = assess_erebus_capture_for_package(historical, capture_id="old")
    assert assessed.classification == "HISTORICAL_REFERENCE"


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_accepts_package_authority(tmp_path: Path) -> None:
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
    assert not any(f"Erebus capture {fixture.capture_id}" in error for error in report.errors)


def test_preview_refuses_invalid_verified_claim(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    _write_summary(mount / CONTROL_DIRNAME, "bad-verified", {
        "status": "CAPTURE_VERIFIED", "active_authority": True,
        "commit": "a" * 40, "tree": "b" * 40,
    })
    _seed_phase(mount)
    report = preview_destination_package(
        mount, run_id=RUN_ID, policy=_preview_policy("bad-verified"),
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
    )
    assert not report.ok
    assert any("Erebus capture bad-verified" in error for error in report.errors)


def test_preview_shows_historical_as_non_authoritative(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    _write_summary(mount / CONTROL_DIRNAME, "historical", {
        "repository": {"commit": "a" * 40, "tree": "b" * 40},
    })
    _seed_phase(mount)
    report = preview_destination_package(
        mount, run_id=RUN_ID, policy=_preview_policy("historical"),
        mercury_commit=MERCURY_COMMIT, mercury_capture_id=MERCURY_CAPTURE,
    )
    assert "historical" in report.included_capture_ids
    assert "erebus_historical_reference=historical" in report.included_git_commits
    assert not any(item.startswith("erebus_package_authority_commit=") for item in report.included_git_commits)


def test_preview_refuses_missing_and_latest(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seed_phase(mount)
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


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_accepts_package_authority(tmp_path: Path, host_state: Path, mount_ok: None) -> None:
    fixture = _golden(tmp_path / "synth")
    mount = tmp_path / "mnt"
    mount.mkdir()
    dest = mount / CONTROL_DIRNAME / "validation" / "erebus" / fixture.capture_id
    dest.parent.mkdir(parents=True)
    fixture.capture_root().rename(dest)
    _seal_with_capture(mount, fixture.capture_id)
    result = create_destination_package(
        mount,
        preview_id=PREVIEW_ID,
        run_id=RUN_ID,
        confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT,
        mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=fixture.commit,
        erebus_capture_id=fixture.capture_id,
        expected_backup_ids=BACKUP_IDS,
        verify_git_head=False,
        package_id="destination_rehearsal_authority_ok",
    )
    assert result.ok, result.errors
    assert (packages_root(mount) / "destination_rehearsal_authority_ok").is_dir()


def test_create_refuses_historical_reference(tmp_path: Path, host_state: Path, mount_ok: None) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _write_summary(mount / CONTROL_DIRNAME, EREBUS_CAPTURE, {
        "repository": {"commit": EREBUS_COMMIT, "tree": "e" * 40},
    })
    _seal_with_capture(mount, EREBUS_CAPTURE)
    result = create_destination_package(
        mount,
        preview_id=PREVIEW_ID,
        run_id=RUN_ID,
        confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT,
        mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=EREBUS_COMMIT,
        erebus_capture_id=EREBUS_CAPTURE,
        expected_backup_ids=BACKUP_IDS,
        verify_git_head=False,
        package_id="destination_rehearsal_historical_refuse",
    )
    assert not result.ok
    assert any("historical and cannot authorize" in error for error in result.errors)
    assert not (packages_root(mount) / "destination_rehearsal_historical_refuse").exists()


def test_create_refuses_missing_without_fixture_flag(tmp_path: Path, host_state: Path, mount_ok: None) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seal_with_capture(mount, EREBUS_CAPTURE)
    result = create_destination_package(
        mount,
        preview_id=PREVIEW_ID,
        run_id=RUN_ID,
        confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT,
        mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=EREBUS_COMMIT,
        erebus_capture_id=EREBUS_CAPTURE,
        expected_backup_ids=BACKUP_IDS,
        verify_git_head=False,
        package_id="destination_rehearsal_missing_refuse",
        allow_synthetic_missing_capture_fixture=False,
    )
    assert not result.ok
    assert any("required capture missing" in error for error in result.errors)
    assert not (packages_root(mount) / "destination_rehearsal_missing_refuse").exists()


def test_create_allows_missing_only_with_injected_fixture_flag(
    tmp_path: Path, host_state: Path, mount_ok: None,
) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    _seal_with_capture(mount, EREBUS_CAPTURE)
    result = create_destination_package(
        mount,
        preview_id=PREVIEW_ID,
        run_id=RUN_ID,
        confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT,
        mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=EREBUS_COMMIT,
        erebus_capture_id=EREBUS_CAPTURE,
        expected_backup_ids=BACKUP_IDS,
        verify_git_head=False,
        package_id="destination_rehearsal_missing_fixture",
        allow_synthetic_missing_capture_fixture=True,
    )
    assert result.ok, result.errors
    assert any("synthetic missing capture fixture allowed" in warning for warning in result.warnings)


def test_create_refuses_tampered_verified_capture(tmp_path: Path, host_state: Path, mount_ok: None) -> None:
    fixture = _golden(tmp_path / "synth")
    mount = tmp_path / "mnt"
    mount.mkdir()
    dest = mount / CONTROL_DIRNAME / "validation" / "erebus" / fixture.capture_id
    dest.parent.mkdir(parents=True)
    fixture.capture_root().rename(dest)
    (dest / "CAPTURE_REPORT.md").write_text("tampered\n")
    _seal_with_capture(mount, fixture.capture_id)
    result = create_destination_package(
        mount,
        preview_id=PREVIEW_ID,
        run_id=RUN_ID,
        confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT,
        mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=fixture.commit,
        erebus_capture_id=fixture.capture_id,
        expected_backup_ids=BACKUP_IDS,
        verify_git_head=False,
        package_id="destination_rehearsal_tamper_refuse",
    )
    assert not result.ok
    assert any("Erebus capture" in error for error in result.errors)
    assert not (packages_root(mount) / "destination_rehearsal_tamper_refuse").exists()


@pytest.mark.parametrize("mutator,needle", [
    ("reconstruction", "reconstruction did not pass"),
    ("phase", "Phase 3B backup identity mismatch"),
    ("recovery", "maintenance recovery mismatch"),
    ("intake", "intake contract hash mismatch"),
    ("supersession", "supersession metadata mismatch"),
    ("authority", "historical and cannot authorize"),
])
def test_create_refuses_specific_authority_faults(
    tmp_path: Path, host_state: Path, mount_ok: None, mutator: str, needle: str,
) -> None:
    fixture = _golden(tmp_path / "synth")
    mount = tmp_path / "mnt"
    mount.mkdir()
    dest = mount / CONTROL_DIRNAME / "validation" / "erebus" / fixture.capture_id
    dest.parent.mkdir(parents=True)
    fixture.capture_root().rename(dest)
    if mutator == "reconstruction":
        data = json.loads((dest / "reconstruction/reconstructed_identity.json").read_text())
        data["head_match"] = False
        (dest / "reconstruction/reconstructed_identity.json").write_text(json.dumps(data))
    elif mutator == "phase":
        data = json.loads((dest / "phase3b_linkage.json").read_text())
        data["backup_ids"] = []
        (dest / "phase3b_linkage.json").write_text(json.dumps(data))
    elif mutator == "recovery":
        data = json.loads((dest / "artifacts/source_recovery/maintenance_source_recovery.json").read_text())
        data["artifact_sha256"] = "0" * 64
        (dest / "artifacts/source_recovery/maintenance_source_recovery.json").write_text(json.dumps(data))
    elif mutator == "intake":
        receipt = json.loads((dest / "manifest_receipt.json").read_text())
        receipt["intake_contract_sha256"] = "0" * 64
        (dest / "manifest_receipt.json").write_text(json.dumps(receipt))
    elif mutator == "supersession":
        data = json.loads((dest / "supersession.json").read_text())
        data["supersedes"] = "wrong"
        (dest / "supersession.json").write_text(json.dumps(data))
    else:
        data = json.loads((dest / "capture_summary.json").read_text())
        data["active_authority"] = False
        data["historical_only"] = True
        (dest / "capture_summary.json").write_text(json.dumps(data))
    _seal_with_capture(mount, fixture.capture_id)
    result = create_destination_package(
        mount,
        preview_id=PREVIEW_ID,
        run_id=RUN_ID,
        confirm=CREATE_CONFIRMATION,
        mercury_commit=MERCURY_COMMIT,
        mercury_capture_id=MERCURY_CAPTURE,
        erebus_commit=fixture.commit,
        erebus_capture_id=fixture.capture_id,
        expected_backup_ids=BACKUP_IDS,
        verify_git_head=False,
        package_id=f"destination_rehearsal_{mutator}_refuse",
    )
    assert not result.ok
    assert any(needle in error for error in result.errors), result.errors
    assert not (packages_root(mount) / f"destination_rehearsal_{mutator}_refuse").exists()

"""Governed Phase 3B restore-check retirement. Never targets managed catalogs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mercury.restore.check_cleanup import RestoreCheckCleanupResult
from mercury.restore.phase3b_restorecheck_retire import (
    CONFIRMATION,
    EREBUS_SCHEMA,
    PI_SCHEMA,
    PROTECTED_NEVER_DROP,
    StaticFacts,
    apply_phase3b_restorecheck_retirement,
    preview_phase3b_restorecheck_retirement,
    proposed_drop_sql,
)

PI = "_restorecheck_pi_fixture_aaaa"
EREBUS = "_restorecheck_erebus_fixture_aaaa"
_DROP_GRANTS = ["'tester'@'localhost' SELECT", "'tester'@'localhost' DROP"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _phase3b(tmp_path: Path, *, erebus_dump: Path, pi_dump: Path, **overrides) -> Path:
    root = tmp_path / "phase3b"
    root.mkdir()
    summary = {
        "run_id": "20260722T055400Z_phase3b",
        "destination_cutover_started": False,
        "zero_unexplained_restore_differences": True,
        "restore_schema_cleanup": "NOT dropped",
        "restore_schemas_retained": [EREBUS_SCHEMA, PI_SCHEMA],
        "dumps": {
            "erebus_threat_intel_prod": {
                "full": str(erebus_dump),
                "full_sha256": _sha(erebus_dump),
                "full_size": erebus_dump.stat().st_size,
                "verified": True,
            },
            "android_permission_intel": {
                "full": str(pi_dump),
                "full_sha256": _sha(pi_dump),
                "full_size": pi_dump.stat().st_size,
                "verified": True,
            },
        },
    }
    summary.update(overrides)
    (root / "phase3b_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (root / "PHASE3B_REPORT.md").write_text("# phase3b\n", encoding="utf-8")
    return root


def _later_backups(root: Path) -> Path:
    backup_root = root / "backups"
    for db in ("erebus_threat_intel_prod", "android_permission_intel"):
        directory = backup_root / "2026-08-28" / db / "20260828_235225_017"
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            json.dumps({"created_at": "2026-08-28T23:52:25Z"}), encoding="utf-8"
        )
        (directory / f"{db}_20260828_235225_017.sql.gz").write_bytes(b"later")
    return backup_root


def _ready_env(tmp_path: Path, *, facts: StaticFacts | None = None):
    erebus_dump = tmp_path / "erebus.sql.gz"
    pi_dump = tmp_path / "pi.sql.gz"
    erebus_dump.write_bytes(b"erebus-dump")
    pi_dump.write_bytes(b"pi-dump")
    phase3b = _phase3b(tmp_path, erebus_dump=erebus_dump, pi_dump=pi_dump)
    backup_root = _later_backups(tmp_path)
    receipt_root = tmp_path / "receipts"
    if facts is None:
        facts = StaticFacts(
            names={PI, EREBUS},
            sizes={PI: 650, EREBUS: 2780},
            grant_map={PI: list(_DROP_GRANTS), EREBUS: list(_DROP_GRANTS)},
        )
    return facts, phase3b, backup_root, receipt_root


def test_proposed_sql_never_includes_dev_or_prod() -> None:
    sql = proposed_drop_sql(PI, EREBUS)
    blob = "\n".join(sql)
    for name in PROTECTED_NEVER_DROP:
        assert name not in blob
    assert sql[0] == f"DROP DATABASE `{PI}`;"
    assert sql[1] == f"DROP DATABASE `{EREBUS}`;"


def test_proposed_sql_refuses_dev() -> None:
    with pytest.raises(ValueError, match="managed catalog"):
        proposed_drop_sql("erebus_threat_intel_dev", EREBUS)


def test_preview_ready(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    result = preview_phase3b_restorecheck_retirement(
        facts=facts,
        phase3b_root=phase3b,
        backup_root=backup_root,
        receipt_root=receipt_root,
        mercury_repo=tmp_path,
        pi_schema=PI,
        erebus_schema=EREBUS,
        git_commit="deadbeef",
        operator="tester",
        hostname="fedora",
    )
    assert result["classification"] == "RESTORECHECK_RETIRE_READY"
    assert result["payload_31221"]["classification"] == "PAYLOAD_31221_IRRECOVERABLE"
    assert result["payload_31221"]["restorecheck_required_for_recovery"] is False
    assert Path(result["path"]).is_file()
    assert "erebus_threat_intel_dev" not in json.dumps(result["proposed_sql"])
    assert result["schemas"]["pi"]["grant_summary"]["drop_present"] is True
    assert result["schemas"]["erebus"]["grant_summary"]["drop_present"] is True
    assert result["schemas"]["pi"]["grant_summary"]["principals"] == ["'tester'@'localhost'"]


def test_preview_refuses_active_connection(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(
        tmp_path,
        facts=StaticFacts(
            names={PI, EREBUS},
            sizes={PI: 1, EREBUS: 1},
            grant_map={PI: list(_DROP_GRANTS), EREBUS: list(_DROP_GRANTS)},
            sessions={PI: 1},
        ),
    )
    result = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_ACTIVE_CONNECTION"


def test_preview_refuses_missing_dump(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    (tmp_path / "erebus.sql.gz").unlink()
    result = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_BACKUP_MISSING"


def test_preview_refuses_digest_mismatch(tmp_path: Path) -> None:
    erebus_dump = tmp_path / "erebus.sql.gz"
    pi_dump = tmp_path / "pi.sql.gz"
    erebus_dump.write_bytes(b"erebus-dump")
    pi_dump.write_bytes(b"pi-dump")
    phase3b = _phase3b(
        tmp_path, erebus_dump=erebus_dump, pi_dump=pi_dump,
        dumps={
            "erebus_threat_intel_prod": {
                "full": str(erebus_dump),
                "full_sha256": "0" * 64,
                "full_size": erebus_dump.stat().st_size,
                "verified": True,
            },
            "android_permission_intel": {
                "full": str(pi_dump),
                "full_sha256": _sha(pi_dump),
                "full_size": pi_dump.stat().st_size,
                "verified": True,
            },
        },
    )
    result = preview_phase3b_restorecheck_retirement(
        facts=StaticFacts(
            names={PI, EREBUS}, sizes={PI: 1, EREBUS: 1},
            grant_map={PI: list(_DROP_GRANTS), EREBUS: list(_DROP_GRANTS)},
        ),
        phase3b_root=phase3b, backup_root=_later_backups(tmp_path),
        receipt_root=tmp_path / "r", mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_BACKUP_DIGEST_MISMATCH"


def test_preview_refuses_dependency(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(
        tmp_path,
        facts=StaticFacts(
            names={PI, EREBUS},
            sizes={PI: 1, EREBUS: 1},
            grant_map={PI: list(_DROP_GRANTS), EREBUS: list(_DROP_GRANTS)},
            dep_map={EREBUS: [{"kind": "view", "schema": "erebus_threat_intel_prod", "name": "v_bad"}]},
        ),
    )
    result = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_DEPENDENCY_FOUND"


def test_preview_refuses_incomplete_phase3b(tmp_path: Path) -> None:
    facts, _, backup_root, receipt_root = _ready_env(tmp_path)
    empty = tmp_path / "empty_phase3b"
    empty.mkdir()
    result = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=empty, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_PHASE3B_EVIDENCE_INCOMPLETE"


def test_preview_refuses_recovery_dependency(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    result = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
        recovery_required=True,
    )
    assert result["classification"] == "RESTORECHECK_RECOVERY_DEPENDENCY_PRESENT"


def test_preview_already_retired(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(
        tmp_path, facts=StaticFacts(names=set()),
    )
    result = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_ALREADY_RETIRED"


def test_apply_requires_confirmation(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    preview = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    with pytest.raises(ValueError, match="confirmation"):
        apply_phase3b_restorecheck_retirement(
            preview_path=Path(preview["path"]),
            confirmation="NOPE",
            facts=facts,
            drop_fn=lambda _name: RestoreCheckCleanupResult(database=_name, dropped=True),
            receipt_root=receipt_root,
            expected_preview_sha256=preview["preview_sha256"],
        )


def test_apply_refuses_mismatched_preview_digest(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    preview = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    with pytest.raises(ValueError, match="preview SHA-256"):
        apply_phase3b_restorecheck_retirement(
            preview_path=Path(preview["path"]),
            confirmation=CONFIRMATION,
            facts=facts,
            drop_fn=lambda _name: RestoreCheckCleanupResult(database=_name, dropped=True),
            receipt_root=receipt_root,
            expected_preview_sha256="0" * 64,
        )


def test_apply_refuses_changed_state(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    preview = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    facts.sessions[PI] = 2
    with pytest.raises(ValueError, match="REFUSE BEFORE DROP"):
        apply_phase3b_restorecheck_retirement(
            preview_path=Path(preview["path"]),
            confirmation=CONFIRMATION,
            facts=facts,
            drop_fn=lambda _name: RestoreCheckCleanupResult(database=_name, dropped=True),
            receipt_root=receipt_root,
            expected_preview_sha256=preview["preview_sha256"],
        )


def test_successful_disposable_two_schema_retirement(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    preview = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )

    def drop(name: str) -> RestoreCheckCleanupResult:
        facts.names.discard(name)
        facts.sizes.pop(name, None)
        return RestoreCheckCleanupResult(database=name, dry_run=False, dropped=True, message="dropped")

    result = apply_phase3b_restorecheck_retirement(
        preview_path=Path(preview["path"]),
        confirmation=CONFIRMATION,
        facts=facts,
        drop_fn=drop,
        receipt_root=receipt_root,
        expected_preview_sha256=preview["preview_sha256"],
    )
    assert result["final_classification"] == "RESTORECHECK_SCHEMAS_RETIRED"
    assert [row["schema"] for row in result["schemas"]] == [PI, EREBUS]
    assert facts.names == set()
    assert result["observed_post_drop_schema_state"] == {
        "pi": {"name": PI, "exists": False},
        "erebus": {"name": EREBUS, "exists": False},
    }


def test_partial_when_second_drop_fails(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    preview = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )

    def drop(name: str) -> RestoreCheckCleanupResult:
        if name == EREBUS:
            return RestoreCheckCleanupResult(database=name, refused=True, message="failed")
        facts.names.discard(name)
        return RestoreCheckCleanupResult(database=name, dry_run=False, dropped=True, message="dropped")

    result = apply_phase3b_restorecheck_retirement(
        preview_path=Path(preview["path"]),
        confirmation=CONFIRMATION,
        facts=facts,
        drop_fn=drop,
        receipt_root=receipt_root,
        expected_preview_sha256=preview["preview_sha256"],
    )
    assert result["final_classification"] == "RESTORECHECK_RETIRE_PARTIAL"
    assert facts.names == {EREBUS}
    assert result["observed_post_drop_schema_state"] == {
        "pi": {"name": PI, "exists": False},
        "erebus": {"name": EREBUS, "exists": True},
    }


def test_repeat_apply_is_not_blind_retry(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(
        tmp_path, facts=StaticFacts(names=set()),
    )
    present = StaticFacts(
        names={PI, EREBUS},
        sizes={PI: 650, EREBUS: 2780},
        grant_map={PI: list(_DROP_GRANTS), EREBUS: list(_DROP_GRANTS)},
    )
    preview = preview_phase3b_restorecheck_retirement(
        facts=present, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    dropped: list[str] = []
    result = apply_phase3b_restorecheck_retirement(
        preview_path=Path(preview["path"]),
        confirmation=CONFIRMATION,
        facts=facts,
        drop_fn=lambda name: dropped.append(name) or RestoreCheckCleanupResult(
            database=name, dropped=True, dry_run=False,
        ),
        receipt_root=receipt_root,
        expected_preview_sha256=preview["preview_sha256"],
    )
    assert result["final_classification"] == "RESTORECHECK_ALREADY_RETIRED"
    assert dropped == []


def test_apply_refuses_dependency_drift(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    preview = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    facts.dep_map[PI] = [
        {"kind": "view", "schema": "erebus_threat_intel_prod", "name": "v_new"},
    ]
    with pytest.raises(ValueError, match="REFUSE BEFORE DROP: production or DEV dependency"):
        apply_phase3b_restorecheck_retirement(
            preview_path=Path(preview["path"]),
            confirmation=CONFIRMATION,
            facts=facts,
            drop_fn=lambda _name: RestoreCheckCleanupResult(database=_name, dropped=True),
            receipt_root=receipt_root,
            expected_preview_sha256=preview["preview_sha256"],
        )


def test_preview_allows_pair_internal_views_but_blocks_dev(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(
        tmp_path,
        facts=StaticFacts(
            names={PI, EREBUS},
            sizes={PI: 1, EREBUS: 1},
            grant_map={PI: list(_DROP_GRANTS), EREBUS: list(_DROP_GRANTS)},
            dep_map={
                PI: [{"kind": "view", "schema": EREBUS, "name": "v_pair"}],
            },
        ),
    )
    ready = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert ready["classification"] == "RESTORECHECK_RETIRE_READY"

    blocked = StaticFacts(
        names={PI, EREBUS},
        sizes={PI: 1, EREBUS: 1},
        dep_map={
            PI: [{"kind": "view", "schema": "erebus_threat_intel_dev", "name": "v_dev"}],
        },
    )
    result = preview_phase3b_restorecheck_retirement(
        facts=blocked, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_DEPENDENCY_FOUND"


def test_preview_refuses_unverified_dump(tmp_path: Path) -> None:
    erebus_dump = tmp_path / "erebus.sql.gz"
    pi_dump = tmp_path / "pi.sql.gz"
    erebus_dump.write_bytes(b"erebus-dump")
    pi_dump.write_bytes(b"pi-dump")
    phase3b = _phase3b(
        tmp_path, erebus_dump=erebus_dump, pi_dump=pi_dump,
        dumps={
            "erebus_threat_intel_prod": {
                "full": str(erebus_dump),
                "full_sha256": _sha(erebus_dump),
                "full_size": erebus_dump.stat().st_size,
                "verified": False,
            },
            "android_permission_intel": {
                "full": str(pi_dump),
                "full_sha256": _sha(pi_dump),
                "full_size": pi_dump.stat().st_size,
                "verified": True,
            },
        },
    )
    result = preview_phase3b_restorecheck_retirement(
        facts=StaticFacts(
            names={PI, EREBUS}, sizes={PI: 1, EREBUS: 1},
            grant_map={PI: list(_DROP_GRANTS), EREBUS: list(_DROP_GRANTS)},
        ),
        phase3b_root=phase3b, backup_root=_later_backups(tmp_path),
        receipt_root=tmp_path / "r", mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_PHASE3B_EVIDENCE_INCOMPLETE"


def test_preview_refuses_missing_drop_grant(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(
        tmp_path,
        facts=StaticFacts(
            names={PI, EREBUS},
            sizes={PI: 1, EREBUS: 1},
            grant_map={PI: ["'tester'@'localhost' SELECT"], EREBUS: list(_DROP_GRANTS)},
        ),
    )
    result = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    assert result["classification"] == "RESTORECHECK_STATE_DRIFT"
    assert any("DROP grant" in item for item in result["blockers"])


def test_apply_refuses_missing_later_backup(tmp_path: Path) -> None:
    facts, phase3b, backup_root, receipt_root = _ready_env(tmp_path)
    preview = preview_phase3b_restorecheck_retirement(
        facts=facts, phase3b_root=phase3b, backup_root=backup_root,
        receipt_root=receipt_root, mercury_repo=tmp_path, pi_schema=PI, erebus_schema=EREBUS,
    )
    later = Path(preview["later_backups"]["erebus_threat_intel_prod"][0]["dump"])
    later.unlink()
    with pytest.raises(ValueError, match="later backup missing"):
        apply_phase3b_restorecheck_retirement(
            preview_path=Path(preview["path"]),
            confirmation=CONFIRMATION,
            facts=facts,
            drop_fn=lambda _name: RestoreCheckCleanupResult(database=_name, dropped=True),
            receipt_root=receipt_root,
            expected_preview_sha256=preview["preview_sha256"],
        )


def test_cli_retire_help_and_apply_refuses_without_live_policy() -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    runner = CliRunner()
    help_result = runner.invoke(app, ["restore-check", "retire-phase3b-restorecheck", "--help"])
    assert help_result.exit_code == 0
    help_text = " ".join(help_result.stdout.split())
    assert "RESTORECHECK SCHEMAS" in help_text
    assert "20260722T055400Z_PHASE3B" in help_text

    # The apply invocation exercises --preview-sha256 through Typer's parser;
    # do not bind this safety test to Rich's environment-dependent option table.
    apply_result = runner.invoke(
        app,
        [
            "restore-check", "retire-phase3b-restorecheck", "--apply",
            "--preview-artifact", "/tmp/missing-preview.json",
            "--preview-sha256", "0" * 64,
            "--confirm", CONFIRMATION,
        ],
    )
    assert apply_result.exit_code != 0
    assert "REFUSE BEFORE DROP" in apply_result.stdout


def test_production_schema_constants_are_restorecheck_only() -> None:
    assert PI_SCHEMA not in PROTECTED_NEVER_DROP
    assert EREBUS_SCHEMA not in PROTECTED_NEVER_DROP
    assert PI_SCHEMA.startswith("_restorecheck_")
    assert EREBUS_SCHEMA.startswith("_restorecheck_")

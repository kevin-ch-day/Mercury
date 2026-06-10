"""M4: schema-only plan, manifest preview, backup layout."""

import json
from datetime import datetime, timezone

import pytest

from mercury.backup.layout import build_backup_layout, planned_backup_directory
from mercury.backup.manifest import BackupManifest, planned_backup_dir, planned_backup_files
from mercury.database.backup_planning import build_backup_plan, build_demo_backup_plan
from mercury.backup.manifest_preview import (
    ManifestPreviewError,
    build_manifest_preview,
    format_manifest_preview_json,
)
from mercury.reporting.terminal.plan import print_schema_backup_plan
from mercury.backup.schema_plan import _schema_plan_from_backup_plan, build_schema_backup_plan_demo
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY


FIXED_DATE = "2026-05-30"
FIXED_TS = "20260530_120000"


def test_schema_plan_includes_prod_databases() -> None:
    plan = build_schema_backup_plan_demo()
    assert "erebus_threat_intel_prod" in plan.sources
    assert "scytaledroid_core_prod" in plan.sources
    assert "obsidiandroid_core_prod" in plan.sources
    assert "gecko_research_database_prod" not in plan.sources


def test_schema_plan_includes_android_permission_intel() -> None:
    plan = build_schema_backup_plan_demo()
    assert "android_permission_intel" in plan.sources


def test_schema_plan_excludes_dev() -> None:
    plan = build_schema_backup_plan_demo()
    excluded_names = {e.name for e in plan.excluded}
    assert "erebus_threat_intel_dev" in excluded_names
    assert "erebus_threat_intel_dev" not in plan.sources


def test_schema_plan_excludes_restorecheck() -> None:
    plan = _schema_plan_from_backup_plan(
        build_backup_plan(["_restorecheck_erebus_threat_intel_prod_20260530"])
    )
    excluded_names = {e.name for e in plan.excluded}
    assert any(n.startswith("_restorecheck_") for n in excluded_names)


def test_schema_plan_excludes_unknown_manual_review() -> None:
    plan = _schema_plan_from_backup_plan(build_backup_plan(["random_test_db"]))
    excluded_names = {e.name for e in plan.excluded}
    assert "random_test_db" in excluded_names


def test_schema_plan_output_format(capsys: pytest.CaptureFixture[str]) -> None:
    print_schema_backup_plan(build_schema_backup_plan_demo())
    out = capsys.readouterr().out
    assert "SCHEMA-ONLY BACKUP PLAN" in out
    assert "Schema backup sources:" in out
    assert "future:" in out
    assert "mercury_backups" in out or "/backups/" in out
    assert ".schema.sql.gz" in out
    assert "Excluded:" in out
    assert "Safety notes:" in out


def test_schema_plan_uses_one_timestamp_per_render(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str | None, str | None]] = []

    original = __import__(
        "mercury.reporting.terminal.plan", fromlist=["build_backup_layout"]
    ).build_backup_layout

    def fake_layout(name: str, *, date=None, timestamp=None, now=None):
        seen.append((date, timestamp))
        return original(name, date=date, timestamp=timestamp, now=now)

    monkeypatch.setattr("mercury.reporting.terminal.plan.build_backup_layout", fake_layout)
    monkeypatch.setattr(
        "mercury.reporting.terminal.plan.datetime",
        type(
            "FixedDateTime",
            (),
            {"now": staticmethod(lambda tz=None: datetime(2026, 5, 30, 12, 0, 0, 123000, tzinfo=timezone.utc))},
        ),
    )
    print_schema_backup_plan(build_schema_backup_plan_demo())
    assert seen
    assert all(date == "2026-05-30" for date, _ in seen)
    assert all(timestamp == "20260530_120000_123" for _, timestamp in seen)


def test_manifest_preview_schema_only_has_schema_gz() -> None:
    preview = build_manifest_preview(
        "erebus_threat_intel_prod",
        BACKUP_KIND_SCHEMA_ONLY,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    assert preview.planned_schema_file is not None
    assert preview.planned_schema_file.endswith(".schema.sql.gz")
    assert preview.planned_dump_file is None
    assert preview.dry_run is True
    assert preview.live_actions_enabled is False


def test_manifest_preview_full_has_sql_gz_and_schema_companion() -> None:
    preview = build_manifest_preview(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    assert preview.planned_dump_file is not None
    assert preview.planned_dump_file.endswith(".sql.gz")
    assert preview.planned_schema_file is not None
    assert preview.planned_schema_file.endswith(".schema.sql.gz")


def test_manifest_preview_refuses_dev_database() -> None:
    with pytest.raises(ManifestPreviewError, match="cannot be a backup source"):
        build_manifest_preview("erebus_threat_intel_dev", BACKUP_KIND_SCHEMA_ONLY)


def test_manifest_preview_json_fields() -> None:
    preview = build_manifest_preview(
        "android_permission_intel",
        BACKUP_KIND_SCHEMA_ONLY,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    data = json.loads(format_manifest_preview_json(preview))
    assert data["database"] == "android_permission_intel"
    assert data["project"] == "Platform"
    assert data["backup_kind"] == "schema_only"
    assert data["manifest_file"].endswith("manifest.json")
    assert data["tool_family"] == "mariadb-dump/mysqldump logical backup"


def test_backup_layout_stable_paths() -> None:
    layout = build_backup_layout(
        "erebus_threat_intel_prod",
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    assert layout.directory == planned_backup_directory(
        "erebus_threat_intel_prod", FIXED_DATE
    )
    assert layout.full_dump_file == "erebus_threat_intel_prod_20260530_120000.sql.gz"
    assert layout.schema_dump_file == (
        "erebus_threat_intel_prod_20260530_120000.schema.sql.gz"
    )
    assert layout.future_schema_hint() == (
        "backups/2026-05-30/erebus_threat_intel_prod/"
        "erebus_threat_intel_prod_20260530_120000.schema.sql.gz"
    )


def test_schema_plan_sources_match_full_plan_backup_sources() -> None:
    full = build_demo_backup_plan()
    schema = build_schema_backup_plan_demo()
    assert schema.sources == full.backup_sources

# merged from test_backup_manifest.py
def test_planned_backup_dir_format() -> None:
    assert planned_backup_dir("erebus_threat_intel_prod", "2026-05-30") == (
        "backups/2026-05-30/erebus_threat_intel_prod/"
    )

# merged from test_backup_manifest.py
def test_build_backup_layout_matches_legacy_helpers() -> None:
    layout = build_backup_layout(
        "erebus_threat_intel_prod",
        date="2026-05-30",
        timestamp="20260530_120000",
    )
    assert layout.directory == planned_backup_dir("erebus_threat_intel_prod", "2026-05-30")
    assert layout.full_dump_file in planned_backup_files(
        "erebus_threat_intel_prod", "20260530_120000"
    )

# merged from test_backup_manifest.py
def test_planned_backup_files_include_manifest() -> None:
    files = planned_backup_files("erebus_threat_intel_prod", "20260530_120000")
    assert "manifest.json" in files
    assert any(f.endswith(".sql.gz") for f in files)

# merged from test_backup_manifest.py
def test_backup_manifest_model() -> None:
    from datetime import datetime, timezone

    m = BackupManifest(
        backup_id="test-1",
        database="erebus_threat_intel_prod",
        backup_kind=BACKUP_KIND_FULL,
        created_at=datetime.now(timezone.utc),
        dump_file="erebus_threat_intel_prod_20260530.sql.gz",
        source_role="production",
    )
    assert m.backup_kind == "full"
    assert m.verified is False


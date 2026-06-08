"""Tests for protection status report."""

from pathlib import Path

from mercury.reporting.protection import build_protection_report, format_protection_report


def test_report_lists_protected_prod_databases() -> None:
    report = build_protection_report()
    assert report.inventory_count == 5
    assert report.ignored_out_of_scope_count == 0
    assert "erebus_threat_intel_prod" in report.protected
    assert "android_permission_intel" in report.protected
    assert "erebus_threat_intel_dev" in report.not_protected
    assert report.manual_review == []


def test_report_includes_prod_dev_pairs() -> None:
    report = build_protection_report()
    prod_names = {p.prod for p in report.prod_dev_pairs}
    assert "erebus_threat_intel_prod" in prod_names
    erebus = next(p for p in report.prod_dev_pairs if p.prod == "erebus_threat_intel_prod")
    assert erebus.dev_listed is True


def test_report_has_action_items() -> None:
    report = build_protection_report()
    assert any("backup" in item.lower() for item in report.action_items)
    assert all("manual review required" not in item.lower() for item in report.action_items)


def test_format_report_contains_sections() -> None:
    text = format_protection_report(build_protection_report())
    assert "PRODUCTION SOURCES" in text
    assert "SHARED AUTHORITY SOURCES" in text
    assert "EXCLUDED FROM BACKUP" in text
    assert "PRODUCTION SYNC PAIRS" in text
    assert "backup-only; no dev sync pair by design" in text
    assert "prod sources" not in text


def test_report_does_not_treat_shared_authority_as_sync_pair() -> None:
    text = format_protection_report(build_protection_report())
    assert "android_permission_intel ->" not in text
    assert "backup-only and do not appear in prod-to-dev sync pairs" in text


def test_status_save_writes_file(tmp_path: Path, monkeypatch) -> None:
    from mercury import protection_report as pr_mod
    from mercury.core.paths import OUTPUT_DIR, PROTECTION_REPORT_FILE

    out = tmp_path / "output"
    report_file = out / "protection_status.txt"
    monkeypatch.setattr("mercury.paths.OUTPUT_DIR", out)
    monkeypatch.setattr("mercury.paths.PROTECTION_REPORT_FILE", report_file)

    text = format_protection_report(build_protection_report())
    out.mkdir(parents=True, exist_ok=True)
    report_file.write_text(text + "\n", encoding="utf-8")
    assert report_file.exists()
    assert "MERCURY PROTECTION STATUS" in report_file.read_text(encoding="utf-8")


def test_live_report_ignores_out_of_scope_databases(monkeypatch) -> None:
    from mercury.database.core import DatabaseInventory, record_from_name
    from mercury.database.core.sources import SOURCE_LIVE

    inventory = DatabaseInventory(
        connection="connected",
        entries=[
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE, connected=True),
            record_from_name("erebus_threat_intel_dev", SOURCE_LIVE, connected=True),
            record_from_name("android_permission_intel", SOURCE_LIVE, connected=True),
            record_from_name("scytaledroid_core_prod", SOURCE_LIVE, connected=True),
            record_from_name("scytaledroid_core_dev", SOURCE_LIVE, connected=True),
            record_from_name("droid_threat_intel_db_prod", SOURCE_LIVE, connected=True),
            record_from_name("proofpoint_cti_db_dev", SOURCE_LIVE, connected=True),
        ],
    )
    monkeypatch.setattr("mercury.reporting.protection.discover", lambda *args, **kwargs: inventory, raising=False)
    monkeypatch.setattr("mercury.database.discovery.discover", lambda *args, **kwargs: inventory)

    report = build_protection_report(live=True)
    assert report.inventory_count == 5
    assert report.ignored_out_of_scope_count == 2
    assert "droid_threat_intel_db_prod" not in report.not_protected
    assert "proofpoint_cti_db_dev" not in report.not_protected


def test_compact_report_does_not_truncate_shared_authority_source(capsys) -> None:
    from mercury.reporting.protection import print_protection_report

    print_protection_report(build_protection_report(), compact=True)
    out = capsys.readouterr().out
    assert "SOURCE DATABASE               SOURCE ROLE              PROJECT" in out
    assert "android_permission_intel      shared authority source  Platform" in out
    assert "Production sources:" in out
    assert "Shared authority:" in out
    assert "Sync pairs:" in out
    assert "shared authority source" in out

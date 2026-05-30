"""Tests for protection status report."""

from pathlib import Path

from mercury.reporting.protection import build_protection_report, format_protection_report


def test_report_lists_protected_prod_databases() -> None:
    report = build_protection_report()
    assert "erebus_threat_intel_prod" in report.protected
    assert "android_permission_intel" in report.protected
    assert "erebus_threat_intel_dev" in report.not_protected


def test_report_includes_prod_dev_pairs() -> None:
    report = build_protection_report()
    prod_names = {p.prod for p in report.prod_dev_pairs}
    assert "erebus_threat_intel_prod" in prod_names
    erebus = next(p for p in report.prod_dev_pairs if p.prod == "erebus_threat_intel_prod")
    assert erebus.dev_listed is True


def test_report_has_action_items() -> None:
    report = build_protection_report()
    assert any("backup" in item.lower() for item in report.action_items)


def test_format_report_contains_sections() -> None:
    text = format_protection_report(build_protection_report())
    assert "PROTECTED" in text
    assert "NOT PROTECTED" in text
    assert "PRODUCTION -> DEVELOPMENT" in text


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

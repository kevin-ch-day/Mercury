"""Tests for pure display formatting helpers."""

import os
import time

from mercury import display_format


def test_format_bytes_scales() -> None:
    assert display_format.format_bytes(512) == "512 B"
    assert "KiB" in display_format.format_bytes(2048)
    assert "MiB" in display_format.format_bytes(5 * 1024 * 1024)


def test_short_path_truncates_long_paths() -> None:
    path = "/very/long/path/" + "x" * 80
    short = display_format.short_path(path, max_len=20)
    assert short.startswith("…")
    assert len(short) == 20


def test_format_pair() -> None:
    assert display_format.format_pair("a_prod", "a_dev") == "a_prod -> a_dev"


def test_format_count_summary() -> None:
    text = display_format.format_count_summary(verified=2, missing=1, failed=0)
    assert text == "2 verified, 1 missing, 0 failed"


def test_format_plan_status() -> None:
    assert display_format.format_plan_status(ready=True) == "ready"
    assert display_format.format_plan_status(ready=False, blockers=["no backup"]) == "no backup"
    assert display_format.format_plan_status(ready=False) == "blocked"


def test_format_report_header() -> None:
    lines = display_format.format_report_header("BACKUP LIST")
    assert lines[0] == "BACKUP LIST"
    assert lines[1] == "-" * 16


def test_format_human_datetime_from_iso() -> None:
    original_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        formatted = display_format.format_human_datetime("2026-06-09T15:01:26+00:00")
        assert "6/9/2026" in formatted
        assert "10:01" in formatted
        assert "AM" in formatted
        assert formatted.endswith("CDT") or formatted.endswith("CST")
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


def test_format_human_datetime_from_datetime() -> None:
    from datetime import datetime, timezone

    original_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        value = datetime(2026, 6, 9, 3, 1, 26, tzinfo=timezone.utc)
        formatted = display_format.format_human_datetime(value)
        assert "6/8/2026" in formatted
        assert "10:01" in formatted
        assert "PM" in formatted
        assert formatted.endswith("CDT") or formatted.endswith("CST")
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

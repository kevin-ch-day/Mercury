"""
Pure formatting helpers for Mercury terminal output.

No I/O — safe to use in tests, reports, and string builders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable


def format_bytes(value: int) -> str:
    """Human-readable byte size."""
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    if value < 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MiB"
    return f"{value / (1024 * 1024 * 1024):.2f} GiB"


def format_human_datetime(value: str | datetime | None) -> str:
    """Human-friendly local terminal timestamp like ``6/9/2026 10:01 AM CDT``."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        instant = value
    else:
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_instant = instant.astimezone()

    hour = local_instant.hour % 12 or 12
    suffix = "AM" if local_instant.hour < 12 else "PM"
    tz_label = local_instant.tzname() or "local"
    return (
        f"{local_instant.month}/{local_instant.day}/{local_instant.year} "
        f"{hour}:{local_instant.minute:02d} {suffix} {tz_label}"
    )


def format_operator_datetime(value: str | datetime | None) -> str:
    """Prose local timestamp for interactive menus: ``July 23, 2026 · 11:12 AM CDT``."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        instant = value
    else:
        try:
            instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)

    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_instant = instant.astimezone()
    hour = local_instant.hour % 12 or 12
    ampm = "AM" if local_instant.hour < 12 else "PM"
    tz_label = local_instant.tzname() or "local"
    return (
        f"{local_instant.strftime('%B')} {local_instant.day}, {local_instant.year} · "
        f"{hour}:{local_instant.minute:02d} {ampm} {tz_label}"
    )


def format_package_id_snapshot(package_id: str) -> str | None:
    """Local snapshot time derived from the trailing ``YYYYMMDDTHHMMSS`` in a package id."""
    import re

    matches = re.findall(r"(\d{8}T\d{6})Z?", package_id or "")
    if not matches:
        return None
    try:
        instant = datetime.strptime(matches[-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return format_operator_datetime(instant)


def format_utc_audit_timestamp(value: str | datetime | None) -> str:
    """UTC audit line: ``UTC: 2026-07-23T16:12:00Z``."""
    if value is None:
        return "UTC: —"
    if isinstance(value, datetime):
        instant = value
    else:
        try:
            instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return f"UTC: {value}"
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    utc = instant.astimezone(timezone.utc)
    return f"UTC: {utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def short_commit(commit: str, *, length: int = 7) -> str:
    text = (commit or "").strip()
    if not text:
        return "—"
    if len(text) <= length:
        return text
    return text[:length] + "…"


def format_compact_human_datetime(value: str | datetime | None) -> str:
    """Compact local timestamp for narrow operator tables like ``6/9 10:01 AM``."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        instant = value
    else:
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_instant = instant.astimezone()

    hour = local_instant.hour % 12 or 12
    suffix = "AM" if local_instant.hour < 12 else "PM"
    return (
        f"{local_instant.month}/{local_instant.day} "
        f"{hour}:{local_instant.minute:02d} {suffix}"
    )


def short_path(path: str, *, max_len: int = 52) -> str:
    """Truncate long paths from the left with an ellipsis."""
    if len(path) <= max_len:
        return path
    return f"…{path[-(max_len - 1):]}"


def truncate_middle(text: str, *, max_len: int) -> str:
    """Truncate from the middle, preserving head and tail identity."""
    value = str(text)
    if max_len <= 0 or len(value) <= max_len:
        return value
    if max_len <= 3:
        return "…"
    head = max(1, (max_len - 1) // 2)
    tail = max_len - head - 1
    return f"{value[:head]}…{value[-tail:]}"


def format_backup_id_display(backup_id: str, *, max_len: int = 40) -> str:
    """Truncate backup IDs while preserving the database prefix and unique suffix.

    Example: ``android_permission_intel…141040_061`` instead of
    ``…rmission_intel-full-20260722_141040_061``.
    """
    text = str(backup_id)
    if max_len <= 0 or len(text) <= max_len:
        return text
    if max_len <= 3:
        return "…"
    # Prefer keeping trailing timestamp uniqueness (after last '-').
    tail_keep = min(12, max_len // 2)
    head_keep = max_len - tail_keep - 1
    if head_keep < 4:
        return f"…{text[-(max_len - 1):]}"
    return f"{text[:head_keep]}…{text[-tail_keep:]}"


def format_pair(source: str, target: str) -> str:
    """Standard prod → dev pair label."""
    return f"{source} -> {target}"


def format_count_summary(**counts: int) -> str:
    """
    Build a compact summary from named counts.

    Example: format_count_summary(verified=2, missing=1, failed=0)
    → "2 verified, 1 missing, 0 failed"
    """
    parts = [f"{value} {name.replace('_', ' ')}" for name, value in counts.items()]
    return ", ".join(parts)


def format_plan_status(*, ready: bool, blockers: Iterable[str] | None = None) -> str:
    """Status cell for sync/restore planning tables."""
    if ready:
        return "ready"
    blockers = list(blockers or [])
    if blockers:
        return blockers[0]
    return "blocked"


def format_verification_status(*, verified: bool) -> str:
    return "verified" if verified else "unverified"


def format_yes_no(value: bool) -> str:
    return "yes" if value else "no"


def format_table(*args, **kwargs):  # noqa: ANN002, ANN003
    """Backward-compatible alias — prefer ``mercury.terminal.table.format_table``."""
    from mercury.terminal.table import format_table as _format_table

    return _format_table(*args, **kwargs)


def format_report_header(title: str, *, width: int | None = None) -> list[str]:
    """ALL-CAPS style report title with underline (verbose CLI reports)."""
    line_width = width if width is not None else max(len(title), 16)
    return [title, "-" * min(line_width, 60)]


def format_dashboard_row(label: str, value: str, *, label_width: int = 14) -> str:
    """Aligned dashboard status row under the main menu."""
    return f"  {label.ljust(label_width)}{value}"


def format_menu_rule(*, width: int = 62) -> str:
    """Horizontal rule for the interactive main menu."""
    return "─" * width


def format_menu_status_row(label: str, tag: str, detail: str, *, label_width: int = 10) -> str:
    """One aligned status row: ``  Mode       [--] dry-run only``."""
    return f"  {label:<{label_width}}{tag} {detail}"


def format_menu_item_row(
    key: str,
    title: str,
    *,
    blurb: str = "",
    title_width: int = 0,
    indent: int = 4,
) -> str:
    """One menu option row with optional trailing description."""
    prefix = " " * indent
    if title_width > 0:
        label = f"[{key}] {title.ljust(title_width)}"
    else:
        label = f"[{key}] {title}"
    if blurb:
        return f"{prefix}{label}  —  {blurb}"
    return f"{prefix}{label}"


def format_menu_section_header(name: str, *, indent: int = 2) -> str:
    """Section title for grouped menu options."""
    return f"{' ' * indent}{name}"

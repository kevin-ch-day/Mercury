"""Synthetic Mercury theme preview (no HDD / DB / host-maintenance access)."""

from __future__ import annotations

from mercury import output
from mercury.terminal.design_system import active_styles, clear_style_cache
from mercury.terminal.format import truncate_middle
from mercury.terminal.theme import (
    THEME_REDLINE,
    danger_banner,
    dashboard_row,
    important_banner,
    menu_header_lines,
    menu_item_line,
    menu_section_header,
    rule_line,
    set_color_enabled,
    set_theme_override,
    strip_markup,
    system_state_header,
    tag,
    tag_plain,
)
from mercury.terminal.theme_settings import validate_theme_id
from mercury.terminal.theme_tokens import THEME_DISPLAY_NAMES
from mercury.terminal.table import Table, TableStyle


def _write_block(title: str, lines: list[str], *, level: str = "normal") -> None:
    output.write("")
    output.write(title)
    output.write(rule_line(width=62, level=level))  # type: ignore[arg-type]
    for line in lines:
        output.write(line)


def _dashboard(state: str) -> list[str]:
    """Title-case descriptive states; keep VERIFIED / DETACHED / READ-ONLY uppercase."""
    rows = {
        "write_enabled": [
            ("Mercury HDD", "Connected · mounted · writes enabled"),
            ("Package", "VERIFIED · destination rehearsal"),
            ("Source", "No changes since package"),
            ("Migration", "Destination validation pending"),
            ("Recommended", "Back up and sync"),
        ],
        "write_disabled": [
            ("Mercury HDD", "Connected · mounted · writes disabled"),
            ("Package", "VERIFIED · destination rehearsal"),
            ("Source", "No changes since package"),
            ("Migration", "Destination validation pending"),
            ("Recommended", "Safely disconnect Mercury HDD"),
        ],
        "safe_disconnect": [
            ("Mercury HDD", "Connected · mounted · writes disabled"),
            ("Package", "VERIFIED"),
            ("Source", "No changes since package"),
            ("Migration", "Destination validation pending"),
            ("Recommended", "Safely disconnect Mercury HDD"),
        ],
        "detached": [
            ("Mercury HDD", "DETACHED"),
            ("Package", "VERIFIED · on media"),
            ("Source", "Unknown while detached"),
            ("Migration", "Destination validation pending"),
            ("Recommended", "Attach HDD and reconnect"),
        ],
        "readonly": [
            ("Mercury HDD", "Connected · mounted · READ-ONLY"),
            ("Package", "VERIFIED"),
            ("Source", "Destination rehearsal host"),
            ("Migration", "Validation active"),
            ("Recommended", "Continue destination validation"),
        ],
    }[state]
    lines = list(system_state_header())
    lines.extend(dashboard_row(label, value) for label, value in rows)
    return lines


def render_theme_preview(
    theme_id: str = THEME_REDLINE,
    *,
    width: int = 100,
    force_color: bool | None = False,
) -> list[str]:
    """Build a full synthetic preview as plain lines (no operational I/O)."""
    validate_theme_id(theme_id)
    captured: list[str] = []

    def _capture(text: str = "") -> None:
        captured.append(strip_markup(str(text)) if text else "")

    try:
        set_theme_override(theme_id)
        clear_style_cache()
        if force_color is not None:
            set_color_enabled(force_color)

        import mercury.core.output as core_output
        import mercury.output as shim_output

        old_core = core_output.write
        old_shim = shim_output.write
        core_output.write = _capture  # type: ignore[assignment]
        shim_output.write = _capture  # type: ignore[assignment]
        try:
            _print_theme_preview_body(theme_id=theme_id, width=width)
        finally:
            core_output.write = old_core
            shim_output.write = old_shim
    finally:
        set_theme_override(None)
        clear_style_cache()
        set_color_enabled(None)

    while captured and captured[-1] == "":
        captured.pop()
    return captured


def _print_theme_preview_body(*, theme_id: str, width: int) -> None:
    """Inner preview body used by print and capture helpers."""
    tid = theme_id
    display = THEME_DISPLAY_NAMES.get(tid, tid)
    output.write(f"THEME PREVIEW · {display} ({tid})")
    output.write(f"Synthetic only · width≈{width} · no HDD / package / host state access")
    output.write(rule_line(width=min(62, width), level="major"))

    # Production header identity (redline_a). Alternatives only for Redline gallery.
    _write_block(
        "1. PRODUCT HEADER (production)",
        menu_header_lines("BACKUP · RECOVERY · MIGRATION"),
        level="major",
    )
    if tid == THEME_REDLINE:
        _write_block(
            "HEADER ALTERNATIVES (preview only)",
            [
                *menu_header_lines("…", variant="redline_b"),
                "",
                *menu_header_lines("…", variant="redline_c"),
            ],
            level="normal",
        )
    else:
        _write_block(
            "HEADER ALTERNATIVE · classic",
            menu_header_lines(
                "Database Backup, Sync, and Disaster Recovery Utility",
                variant="classic",
            ),
            level="normal",
        )

    _write_block("2. NORMAL DASHBOARD", _dashboard("write_disabled"), level="normal")
    _write_block("3. MOUNTED / WRITE-ENABLED", _dashboard("write_enabled"), level="normal")
    _write_block("4. MOUNTED / WRITE-DISABLED", _dashboard("write_disabled"), level="normal")
    _write_block("5. SAFE-DISCONNECT READY", _dashboard("safe_disconnect"), level="normal")
    _write_block("6. DETACHED", _dashboard("detached"), level="normal")
    _write_block("7. READ-ONLY DESTINATION", _dashboard("readonly"), level="normal")

    menu_lines = [
        menu_section_header("OPERATIONS", indent=0),
        rule_line(width=62, level="normal"),
        menu_item_line("1", "Safely disconnect Mercury HDD", indent=2, recommended=True),
        menu_item_line("2", "Back up and sync again", indent=2),
        menu_item_line("3", "Prepare destination move", indent=2),
        menu_item_line("4", "Browse all operations", indent=2),
        menu_item_line(
            "5",
            "Build Migration Package (writes disabled)",
            indent=2,
            disabled=True,
        ),
        menu_item_line("0", "Exit", indent=2),
        "",
        "Choice:",
    ]
    _write_block("8–10. MENU / RECOMMENDED / UNAVAILABLE", menu_lines, level="normal")

    _write_block(
        "DESTRUCTIVE ACTION (isolated example)",
        [
            menu_item_line("9", "Drop production database", indent=2, destructive=True),
        ],
        level="major",
    )

    _write_block(
        "11. SUCCESS",
        [tag("ok", "Destination package verified"), tag("ok", "Mercury capture reconstructed")],
        level="normal",
    )
    _write_block(
        "12. WARNING",
        [
            *important_banner("IMPORTANT"),
            "A verified destination package already exists.",
            "",
            tag("warn", "Source writes remain disabled"),
            "Enabling writes allows newer recovery artifacts outside the package.",
        ],
        level="normal",
    )
    danger = danger_banner("CONFIRM SOURCE WRITER RESTORE")
    _write_block(
        "13. DANGER CONFIRMATION",
        [
            *danger,
            "This changes Mercury from rehearsal protection to active source writing.",
            "",
            "Type exactly:",
            "  RESTORE SOURCE WRITER",
            "",
            "Confirmation:",
        ],
        level="major",
    )

    table = Table.from_headers(
        ["DATABASE", "FRESHNESS", "RESTORE CHECK", "SIZE"],
        [
            ["android_permission_intel", "Fresh", "Not checked", "26.29 MiB"],
            ["erebus_threat_intel_prod", "Fresh", "Phase 3B only", "283.12 MiB"],
            [
                truncate_middle(
                    "scytaledroid_core_prod-full-20260722_055507_238_extra_long_id",
                    max_len=36,
                ),
                "Stale",
                "Failed",
                "1.02 GiB",
            ],
        ],
        style=TableStyle(indent=0),
        min_col_widths=[28, 10, 14, 10],
    )
    _write_block("14. DATABASE TABLE", table.lines(), level="minor")

    phase_lines = [
        "PACKAGE VERIFICATION  04/07",
        rule_line(width=62, level="normal"),
        tag_plain("ok", "Preview identity"),
        tag_plain("ok", "Mercury capture"),
        tag_plain("ok", "Erebus capture"),
        "[RUN ] Package checksums",
        "[WAIT] Intake subset",
        "[WAIT] Evidence inventory",
        "[WAIT] Final receipt",
    ]
    _write_block("15. PROGRESS PHASES", phase_lines, level="normal")

    long_id = (
        "erebus_threat_intel_prod-full-20260722_055507_238_"
        "destination_rehearsal_final_source_20260723T161213Z"
    )
    _write_block(
        "16. LONG BACKUP IDS",
        [
            f"full:  {long_id}",
            f"mid:   {truncate_middle(long_id, max_len=52)}",
            f"80col: {truncate_middle(long_id, max_len=40)}",
        ],
        level="minor",
    )

    narrow = [
        menu_header_lines("BACKUP · RECOVERY · MIGRATION")[0],
        rule_line(width=min(40, width), level="major"),
        dashboard_row("HDD", "Writes disabled", label_width=8),
        dashboard_row("Pkg", "VERIFIED", label_width=8),
        menu_item_line("1", "Safe disconnect", indent=2, recommended=True),
    ]
    _write_block("17. NARROW TERMINAL (~40–80)", narrow, level="normal")

    _write_block(
        "18. STATUS LABELS (COLOR-INDEPENDENT)",
        [
            tag_plain("ok", "verified"),
            tag_plain("warn", "writes disabled"),
            tag_plain("fail", "identity mismatch"),
            tag_plain("info", "Phase 3B remains authority"),
        ],
        level="minor",
    )

    output.write("")
    output.write("Preview complete. No operational state was read or changed.")


def print_theme_preview(*, theme_id: str | None = None, width: int = 100) -> None:
    """Print the synthetic theme gallery to the operator console."""
    tid = validate_theme_id(theme_id) if theme_id else active_styles().theme_id
    set_theme_override(tid)
    clear_style_cache()
    try:
        _print_theme_preview_body(theme_id=tid, width=width)
    finally:
        set_theme_override(None)
        clear_style_cache()

"""Safe-disconnect introduction screens (presentation only; no detach logic)."""

from __future__ import annotations

from mercury import output
from mercury.terminal.format import truncate_middle
from mercury.terminal.theme import (
    colors_enabled,
    dashboard_row,
    markup,
    rule_line,
    section_title,
)
from mercury.terminal.design_system import active_styles


def _section(title: str) -> None:
    output.write("")
    if colors_enabled():
        output.write(section_title(title))
    else:
        output.write(title)
    output.write(rule_line(level="normal"))


def render_safe_disconnect_intro(*, identity) -> list[str]:
    """Return ANSI-free structural lines for tests / capture (no I/O)."""
    lines: list[str] = []
    lines.append("SAFE DISCONNECT MERCURY HDD")
    lines.append("-" * 62)
    if identity is None:
        lines.append("TARGET DEVICE")
        lines.append("  (not resolved)")
    else:
        uuid = truncate_middle(str(identity.uuid or ""), max_len=22)
        partition = str(identity.partition_device or "—")
        parent = str(identity.parent_device or "—")
        device = f"{partition} → {parent}" if parent and parent != "—" else partition
        lines.extend(
            [
                "TARGET DEVICE",
                f"  Storage      {identity.label or '—'}",
                f"  Hardware     {identity.model or '—'}",
                f"  Device       {device}",
                f"  Mount        {identity.mountpoint or '(not mounted)'}",
                f"  UUID         {uuid or '—'}",
            ]
        )
    lines.extend(
        [
            "DETACH SEQUENCE",
            "  01  Check active processes",
            "  02  Flush filesystem writes",
            "  03  Unmount Mercury storage",
            "  04  Verify UUID is detached",
            "  05  Power off verified parent device",
            "PROTECTED",
            "  MERCURY_DATA_USB will not be touched",
            "  Phase 3B evidence will not be modified",
            "  Erebus will remain paused",
            "PRE-FLIGHT AUTHORIZATION",
        ]
    )
    return lines


def print_safe_disconnect_intro(*, identity) -> None:
    """Print the Safe Disconnect confirmation chrome (checks unchanged)."""
    styles = active_styles()
    title = "SAFE DISCONNECT MERCURY HDD"
    if colors_enabled():
        output.write(section_title(title))
    else:
        output.write(title)
    output.write(rule_line(level="major"))

    _section("TARGET DEVICE")
    if identity is None:
        output.write("  (device identity not resolved)")
    else:
        uuid = truncate_middle(str(identity.uuid or ""), max_len=22)
        partition = str(identity.partition_device or "—")
        parent = str(identity.parent_device or "—")
        device = f"{partition} → {parent}" if parent and parent != "—" else partition
        for line in (
            dashboard_row("Storage", identity.label or "—", label_width=12),
            dashboard_row("Hardware", identity.model or "—", label_width=12),
            dashboard_row("Device", device, label_width=12),
            dashboard_row("Mount", identity.mountpoint or "(not mounted)", label_width=12),
            dashboard_row("UUID", uuid or "—", label_width=12),
        ):
            output.write(line)

    _section("DETACH SEQUENCE")
    steps = (
        "Check active processes",
        "Flush filesystem writes",
        "Unmount Mercury storage",
        "Verify UUID is detached",
        "Power off verified parent device",
    )
    for index, step in enumerate(steps, start=1):
        num = f"{index:02d}"
        if colors_enabled():
            output.write(f"  {markup(num, styles.menu_key)}  {markup(step, styles.value)}")
        else:
            output.write(f"  {num}  {step}")

    _section("PROTECTED")
    protected = (
        "MERCURY_DATA_USB will not be touched",
        "Phase 3B evidence will not be modified",
        "Erebus will remain paused",
    )
    for item in protected:
        if colors_enabled():
            output.write(f"  {markup(item, styles.value_muted)}")
        else:
            output.write(f"  {item}")

    _section("PRE-FLIGHT AUTHORIZATION")

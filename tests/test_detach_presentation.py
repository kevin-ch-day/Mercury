"""Presentation tests for Safe Disconnect intro chrome."""

from __future__ import annotations

from types import SimpleNamespace

from mercury.storage.detach_presentation import render_safe_disconnect_intro


def test_safe_disconnect_intro_is_compact_and_protective() -> None:
    identity = SimpleNamespace(
        label="MERCURY_DATA_V2",
        uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        model="WDC WD10JDRW-11CFYS0",
        partition_device="/dev/sdb1",
        parent_device="/dev/sdb",
        mountpoint="/mnt/MERCURY_DATA_V2",
    )
    text = "\n".join(render_safe_disconnect_intro(identity=identity))
    assert "TARGET DEVICE" in text
    assert "DETACH SEQUENCE" in text
    assert "PROTECTED" in text
    assert "PRE-FLIGHT AUTHORIZATION" in text
    assert "01  Check active processes" in text
    assert "MERCURY_DATA_USB will not be touched" in text
    assert "Phase 3B" in text
    assert "This operation will:" not in text
    assert "715f29a9" in text
    # Middle-truncated UUID on the confirmation screen.
    assert "715f29a9-2671-477b-8c8d-515d190addb9" not in text


def test_safe_disconnect_wizard_uses_new_prompt(monkeypatch) -> None:
    from mercury.storage import interactive_menu as menu

    calls: list[str] = []

    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **_k: SimpleNamespace(
            identity=SimpleNamespace(
                label="MERCURY_DATA_V2",
                uuid="715f29a9-2671-477b-8c8d-515d190addb9",
                model="WDC",
                partition_device="/dev/sdb1",
                parent_device="/dev/sdb",
                mountpoint="/mnt/MERCURY_DATA_V2",
            ),
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_yes_no",
        lambda prompt, default=False: calls.append(prompt) or False,
    )
    monkeypatch.setattr(menu.display_screen, "write_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(menu.display_screen, "write_status", lambda *_a, **_k: None)
    monkeypatch.setattr(menu.output, "write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "mercury.storage.detach_presentation.print_safe_disconnect_intro",
        lambda **_k: None,
    )

    assert menu._run_safe_disconnect_wizard_impl() is False
    assert any("safe-disconnect checks" in p for p in calls)
    assert "Continue with preflight?" not in calls

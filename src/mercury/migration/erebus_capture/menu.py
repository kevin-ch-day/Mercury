"""Interactive route for the governed Erebus source capture."""

from __future__ import annotations

import json
from pathlib import Path

from mercury import output
from mercury.menu import prompts as menu_prompts

from .context import CaptureContext
from .models import ErebusCaptureRequest
from .service import create_preview, execute_capture, load_preview
from .storage_preflight import StorageFacts

EXECUTE_AVAILABILITY = (
    "production execute locked unless a host-local authorization receipt is supplied"
)


def _ask(label: str) -> str:
    return (menu_prompts.ask(label) or "").strip()


def ready_preview_ids(control_root: Path) -> list[str]:
    """Return exact preview IDs that currently load as READY."""
    root = control_root / "validation" / "previews" / "erebus"
    if not root.is_dir():
        return []
    ready: list[str] = []
    for preview in sorted(
        path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")
    ):
        if load_preview(control_root, preview.name).ok:
            ready.append(preview.name)
    return ready


def menu_options(control_root: Path) -> list[tuple[str, str]]:
    """Preview and review always; execute only when a READY receipt exists."""
    options = [("1", "Preview capture"), ("2", "Review previews")]
    if ready_preview_ids(control_root):
        options.append(("3", "Create approved capture"))
    return options


def _preview_from_prompts() -> None:
    """Collect explicit inputs and invoke the same service used by the CLI."""
    values = {
        "preview_id": _ask("Preview ID"), "repository": _ask("Repository"),
        "capture_id": _ask("Capture ID"), "expected_commit": _ask("Expected commit"),
        "expected_tree": _ask("Expected tree"), "phase3b_run_id": _ask("Phase 3B run ID"),
        "maintenance_sha256": _ask("maintenance.py SHA-256"),
    }
    recovery = Path(_ask("Recovery receipt"))
    phase = Path(_ask("Phase 3B evidence root"))
    intake = Path(_ask("Intake contract"))
    control = Path(_ask("Control root"))
    facts_path = Path(_ask("Reviewed storage facts JSON"))
    try:
        facts = StorageFacts(**json.loads(facts_path.read_text(encoding="utf-8")))
        request = ErebusCaptureRequest(**values)
        context = CaptureContext(control, Path(values["repository"]), recovery, phase, intake, lambda: facts)
        result = create_preview(context, request)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        output.write(f"REFUSED: {exc}")
        return
    if result.ok:
        output.write(f"PREVIEW READY: {result.preview_id}")
        output.write(f"Final intended path: {control / 'validation' / 'erebus' / values['capture_id']}")
        output.write(f"Execute availability: {EXECUTE_AVAILABILITY}")
    else:
        output.write("REFUSED: " + ", ".join(result.reason_codes or result.errors))


def _review_previews(control: Path) -> None:
    root = control / "validation" / "previews" / "erebus"
    if not root.is_dir():
        output.write("No Erebus previews at that control root.")
        return
    for preview in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        result = load_preview(control, preview.name)
        output.write(f"{preview.name}: {result.classification} {'OK' if result.ok else ', '.join(result.errors)}")


def _execute_from_prompts(control: Path) -> None:
    """Revalidate one exact READY preview through the shared production lock."""
    preview_id = _ask("Exact READY preview ID")
    repo = Path(_ask("Repository"))
    recovery = Path(_ask("Recovery receipt"))
    phase = Path(_ask("Phase 3B evidence root"))
    intake = Path(_ask("Intake contract"))
    facts_path = Path(_ask("Reviewed storage facts JSON"))
    try:
        facts = StorageFacts(**json.loads(facts_path.read_text(encoding="utf-8")))
        result = execute_capture(
            CaptureContext(control, repo, recovery, phase, intake, lambda: facts),
            preview_id,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        output.write(f"CAPTURE EXECUTION REFUSED: {exc}")
        return
    output.write("CAPTURE VERIFIED" if result.ok else "CAPTURE EXECUTION REFUSED")
    output.write(result.classification if not result.ok else preview_id)


def run_erebus_source_capture_menu() -> None:
    """Show Preview/Review always; expose execute only while READY receipts exist."""
    from mercury.menu.task_menus import _submenu

    control = Path(_ask("Control root"))
    while True:
        options = menu_options(control)
        choice = _submenu("Erebus source capture", options)
        if choice is None:
            return
        if choice == "1":
            _preview_from_prompts()
        elif choice == "2":
            _review_previews(control)
        elif choice == "3" and any(key == "3" for key, _label in options):
            _execute_from_prompts(control)
        else:
            output.write(menu_prompts.invalid_choice_message(choice))

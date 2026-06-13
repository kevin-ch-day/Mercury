"""
Shared interactive prompts for the Mercury menu.

Low-level input lives here. Screen formatting is in ``menu_display``;
the read-eval loop is in ``menu_interactive``.

All user input (option selection, return pauses, y/n, confirmation phrases)
should go through this module so behavior and test seams stay consistent.
"""

from __future__ import annotations

from collections.abc import Callable

from mercury import output
from mercury.terminal.prompts import input_prompt, normalize_input_prompt
from mercury.terminal.theme import continue_prompt
from mercury.menu.main_display import MENU_SECTIONS

PromptReader = Callable[[str], str]
ContinueReader = Callable[[], None]

_reader: PromptReader | None = None
_continue_reader: ContinueReader | None = None

CONTINUE_PROMPT = "\nPress any key to continue..."

QUIT_ALIASES = frozenset({"q", "quit", "exit"})


def menu_action_keys() -> list[str]:
    """Numeric keys for configured menu actions (excludes exit)."""
    return [item.key for _section, items in MENU_SECTIONS for item in items]


def menu_option_range_label() -> str:
    """Human-readable option range, e.g. ``0-6``."""
    keys = menu_action_keys()
    if not keys:
        return "0"
    return f"0-{max(int(key) for key in keys)}"


def menu_option_prompt() -> str:
    """Prompt text for menu selection."""
    return input_prompt("\nEnter your choice: ")


def submenu_option_prompt() -> str:
    """Prompt text for action submenus (distinct from the main menu)."""
    return input_prompt("\nChoice: ")


MENU_RETURN_PROMPT = menu_option_prompt()


def set_prompt_reader(reader: PromptReader | None) -> None:
    """Redirect input (for tests). Pass None to reset to built-in input()."""
    global _reader
    _reader = reader


def set_continue_reader(reader: ContinueReader | None) -> None:
    """Redirect continue pauses (for tests). Pass None to reset to built-in behavior."""
    global _continue_reader
    _continue_reader = reader


def ask(prompt: str) -> str:
    """Read one line of user input."""
    normalized = normalize_input_prompt(prompt)
    if _reader is not None:
        return _reader(normalized)
    print(normalized, end="", flush=True)
    return input()


def ask_safe(prompt: str) -> str | None:
    """Read input; None on EOF or Ctrl+C (prints a blank line first)."""
    try:
        return ask(prompt)
    except (EOFError, KeyboardInterrupt):
        output.write()
        return None


def ask_stripped(prompt: str) -> str | None:
    """Read one line, stripped; None on EOF or Ctrl+C."""
    value = ask_safe(prompt)
    if value is None:
        return None
    return value.strip()


def normalize_menu_choice(raw: str) -> str:
    """Map quit aliases to exit and strip whitespace."""
    choice = raw.strip().lower()
    if choice in QUIT_ALIASES:
        return "0"
    return choice


def is_valid_menu_choice(raw: str) -> bool:
    """True when raw input is exit or a configured menu action key."""
    normalized = normalize_menu_choice(raw)
    if not normalized:
        return False
    if normalized == "0":
        return True
    return normalized in menu_action_keys()


def invalid_choice_message(raw: str) -> str:
    """Standard invalid-selection message for the current menu layout."""
    from mercury.repair.startup import main_menu_invalid_choice_suffix

    extra = main_menu_invalid_choice_suffix()
    return f"Invalid choice: {raw!r}. Enter {menu_option_range_label()}{extra} or q to exit."


def read_menu_option() -> str | None:
    """Read the main menu option; None on EOF or Ctrl+C."""
    return ask_stripped(menu_option_prompt())


def wait_for_continue(*, prompt: str = CONTINUE_PROMPT) -> None:
    """Pause after a menu action until the user presses a key."""
    if _continue_reader is not None:
        _continue_reader()
        return

    import sys

    if not sys.stdin.isatty():
        ask_safe(prompt)
        return

    shown = continue_prompt() if prompt == CONTINUE_PROMPT else normalize_input_prompt(prompt)
    output.write(shown)
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except ImportError:
        ask_safe(prompt)
    except OSError:
        ask_safe(prompt)
    output.write("")


def ask_yes_no(prompt: str, *, default: bool | None = None) -> bool | None:
    """
    Ask a yes/no question.

    Returns True/False, or None on interrupt. Empty input uses ``default`` when set.
    """
    if default is True:
        suffix = " [Y/n]: "
    elif default is False:
        suffix = " [y/N]: "
    else:
        suffix = " [y/n]: "

    while True:
        raw = ask_stripped(f"{prompt}{suffix}")
        if raw is None:
            return None
        if not raw:
            if default is not None:
                return default
            output.write("Enter y or n.")
            continue
        lower = raw.lower()
        if lower in {"y", "yes"}:
            return True
        if lower in {"n", "no"}:
            return False
        output.write("Enter y or n.")


def ask_confirmation_phrase(expected: str, *, action: str = "continue") -> bool:
    """
    Require an exact confirmation phrase (e.g. ``SYNC DEV``).

    Returns False on mismatch or interrupt.
    """
    prompt = f"\nConfirmation ({action}) [{expected}]: "
    raw = ask_stripped(prompt)
    if raw is None:
        return False
    return raw == expected

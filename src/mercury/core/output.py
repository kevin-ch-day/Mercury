"""Plain-text CLI output — no boxes; portable on Windows and Fedora."""

import sys
from typing import TextIO

_stream: TextIO | None = None


def _configure_stdio() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except (OSError, ValueError):
                    pass


_configure_stdio()


def set_stream(stream: TextIO | None) -> None:
    """Redirect output (for tests). Pass None to reset to stdout."""
    global _stream
    _stream = stream


def _out() -> TextIO:
    return _stream if _stream is not None else sys.stdout


def write(text: str = "") -> None:
    print(text, file=_out())


def heading(text: str) -> None:
    write()
    write(text)


def field(name: str, value: object) -> None:
    write(f"  {name}: {value}")


def bullet(text: str) -> None:
    write(f"  - {text}")


def item(text: str, indent: int = 2) -> None:
    write(f"{' ' * indent}{text}")

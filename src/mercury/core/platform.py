"""Shared operating-system detection and support policy for Mercury."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform


@dataclass(frozen=True)
class PlatformInfo:
    system: str
    release: str
    distro_id: str | None = None
    distro_name: str | None = None

    @property
    def is_windows(self) -> bool:
        return self.system == "Windows"

    @property
    def is_linux(self) -> bool:
        return self.system == "Linux"

    @property
    def is_fedora(self) -> bool:
        return self.is_linux and (self.distro_id or "").lower() == "fedora"

    @property
    def support_label(self) -> str:
        if self.is_fedora:
            return "Fedora supported"
        if self.is_windows:
            return "Windows seed-only"
        if self.is_linux:
            return "Linux non-Fedora"
        return f"{self.system} unsupported"

    @property
    def allows_live_execution(self) -> bool:
        return self.is_fedora

    @property
    def operator_note(self) -> str:
        if self.is_fedora:
            return "Fedora detected — primary Mercury runtime."
        if self.is_windows:
            return "Windows detected — seed planning/status only; live Fedora workflows are not supported."
        if self.is_linux:
            distro = self.distro_name or self.distro_id or "Linux"
            return f"{distro} detected — Mercury is Fedora-first; verify paths and tooling before live use."
        return f"{self.system} detected — Mercury targets Fedora for live operations."


def _parse_os_release(path: Path = Path("/etc/os-release")) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    distro_id: str | None = None
    distro_name: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized = value.strip().strip('"')
        if key == "ID":
            distro_id = normalized
        elif key == "NAME":
            distro_name = normalized
    return distro_id, distro_name


def detect_platform() -> PlatformInfo:
    system = platform.system()
    release = platform.release()
    distro_id: str | None = None
    distro_name: str | None = None
    if system == "Linux":
        distro_id, distro_name = _parse_os_release()
    return PlatformInfo(
        system=system,
        release=release,
        distro_id=distro_id,
        distro_name=distro_name,
    )

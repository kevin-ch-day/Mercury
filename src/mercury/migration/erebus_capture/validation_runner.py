"""Injected, structured validation-command boundary for synthetic captures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ValidationResult:
    command: tuple[str, ...]
    cwd: str
    return_code: int
    stdout: str = ""
    stderr: str = ""
    started: bool = True
    completed: bool = True
    parsed: dict[str, object] | None = None

    @property
    def accepted(self) -> bool:
        return self.started and self.completed and self.return_code == 0

    def evidence(self) -> dict[str, object]:
        return {**asdict(self), "accepted": self.accepted}


class ValidationRunner(Protocol):
    def run(self, name: str, *, cwd: Path, command: tuple[str, ...]) -> ValidationResult: ...


class DeterministicValidationRunner:
    """Test-only runner whose named results are explicitly supplied/injected."""
    def __init__(self, results: dict[str, ValidationResult] | None = None) -> None:
        self.results = results or {}

    def run(self, name: str, *, cwd: Path, command: tuple[str, ...]) -> ValidationResult:
        return self.results.get(name, ValidationResult(command, str(cwd), 0, stdout="synthetic PASS\n", parsed={"name": name}))

"""Injected, structured validation-command boundary for capture writers."""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


_SUMMARY_RE = re.compile(
    r"(?P<failed>\d+)\s+failed|"
    r"(?P<passed>\d+)\s+passed|"
    r"(?P<skipped>\d+)\s+skipped|"
    r"(?P<collected>\d+)\s+collected|"
    r"(?P<error>\d+)\s+error",
    re.IGNORECASE,
)
_FAILED_NODE_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)", re.MULTILINE)


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
        return self.results.get(
            name,
            ValidationResult(command, str(cwd), 0, stdout="synthetic PASS\n", parsed={"name": name}),
        )


def parse_pytest_output(stdout: str, stderr: str = "") -> dict[str, object]:
    """Extract collected/passed/skipped/failed counts and failing node ids from pytest text."""
    text = f"{stdout}\n{stderr}"
    collected = passed = skipped = failed = errors = 0
    for match in _SUMMARY_RE.finditer(text):
        if match.group("collected"):
            collected = int(match.group("collected"))
        if match.group("passed"):
            passed = int(match.group("passed"))
        if match.group("skipped"):
            skipped = int(match.group("skipped"))
        if match.group("failed"):
            failed = int(match.group("failed"))
        if match.group("error"):
            errors = int(match.group("error"))
    failing_nodes = [node for _kind, node in _FAILED_NODE_RE.findall(text)]
    failures = [
        {
            "node_id": node,
            "classification": "host_output",
            "disposition": "accepted_unrelated",
        }
        for node in failing_nodes
    ]
    return {
        "collected_count": collected or (passed + skipped + failed + errors),
        "passed_count": passed,
        "skipped_count": skipped,
        "failed_count": failed + errors,
        "failures": failures,
        "collection_errors": errors if "ERROR collecting" in text or "Interrupted" in text else 0,
    }


class SubprocessValidationRunner:
    """Production runner that executes the named validation command in the source repo."""

    def __init__(self, *, timeout_seconds: int = 3600) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, name: str, *, cwd: Path, command: tuple[str, ...]) -> ValidationResult:
        try:
            completed = subprocess.run(
                list(command),
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            return ValidationResult(
                command, str(cwd), 127, stderr=str(exc), started=True, completed=False, parsed={"name": name},
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return ValidationResult(
                command, str(cwd), 124, stdout=stdout, stderr=stderr or "timeout",
                started=True, completed=False, parsed={"name": name},
            )
        parsed: dict[str, object] = {"name": name}
        if "pytest" in command:
            parsed.update(parse_pytest_output(completed.stdout, completed.stderr))
        return ValidationResult(
            command,
            str(cwd),
            completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            started=True,
            completed=True,
            parsed=parsed,
        )

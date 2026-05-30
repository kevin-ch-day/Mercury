"""Log file analysis — status, sessions, and counts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mercury.logging.config import (
    BACKUP_LOG_FILENAME,
    DATABASE_LOG_FILENAME,
    ERROR_LOG_FILENAME,
    logging_enabled,
    resolve_log_dir,
)
from mercury.logging.utils import list_all_log_files, list_log_files

SESSION_START_RE = re.compile(r"session start id=(\S+)")
SESSION_END_RE = re.compile(r"session end id=(\S+) exit_code=(\d+)")


@dataclass(frozen=True)
class LogFileInfo:
    name: str
    path: Path
    size_bytes: int
    lines: int
    errors: int
    warnings: int
    modified_at: str


@dataclass(frozen=True)
class LogSession:
    session_id: str
    start_line: str
    end_line: str | None = None
    exit_code: int | None = None
    command: str | None = None


@dataclass
class LogStatusReport:
    log_dir: Path
    logging_enabled: bool
    files: list[LogFileInfo] = field(default_factory=list)
    sessions: list[LogSession] = field(default_factory=list)
    total_errors: int = 0
    total_warnings: int = 0


def _count_levels(lines: list[str]) -> tuple[int, int]:
    errors = sum(1 for line in lines if " ERROR " in line or line.endswith(" ERROR"))
    warnings = sum(1 for line in lines if " WARNING " in line)
    return errors, warnings


def analyze_log_file(path: Path) -> LogFileInfo:
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    lines = text.splitlines()
    errors, warnings = _count_levels(lines)
    modified = (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        if path.is_file()
        else ""
    )
    return LogFileInfo(
        name=path.name,
        path=path,
        size_bytes=path.stat().st_size if path.is_file() else 0,
        lines=len(lines),
        errors=errors,
        warnings=warnings,
        modified_at=modified,
    )


def parse_recent_sessions(*, log_dir: Path | None = None, max_sessions: int = 10) -> list[LogSession]:
    """Extract recent session start/end pairs from main daily logs."""
    directory = log_dir or resolve_log_dir()
    open_sessions: dict[str, LogSession] = {}
    ordered: list[LogSession] = []

    for path in list_log_files(log_dir=directory):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            start_match = SESSION_START_RE.search(line)
            if start_match:
                sid = start_match.group(1)
                command = _extract_command(line)
                session = LogSession(session_id=sid, start_line=line.strip(), command=command)
                open_sessions[sid] = session
                ordered.append(session)
                continue
            end_match = SESSION_END_RE.search(line)
            if end_match:
                sid = end_match.group(1)
                exit_code = int(end_match.group(2))
                session = open_sessions.get(sid)
                if session is None:
                    session = LogSession(session_id=sid, start_line="")
                    ordered.append(session)
                open_sessions[sid] = LogSession(
                    session_id=sid,
                    start_line=session.start_line,
                    end_line=line.strip(),
                    exit_code=exit_code,
                    command=session.command,
                )

    merged: dict[str, LogSession] = {}
    for session in ordered:
        merged[session.session_id] = open_sessions.get(session.session_id, session)

    sessions = list(merged.values())
    sessions.sort(key=lambda item: item.session_id, reverse=True)
    return sessions[:max_sessions]


def _extract_command(line: str) -> str | None:
    marker = "command="
    if marker not in line:
        return None
    rest = line.split(marker, 1)[1]
    for token in (" dry_run=", " live_actions=", " backup_root="):
        if token in rest:
            return rest.split(token, 1)[0].strip()
    return rest.strip()


def build_log_status(*, log_dir: Path | None = None) -> LogStatusReport:
    directory = log_dir or resolve_log_dir()
    report = LogStatusReport(log_dir=directory, logging_enabled=logging_enabled())

    if not directory.is_dir():
        return report

    known = [ERROR_LOG_FILENAME, DATABASE_LOG_FILENAME, BACKUP_LOG_FILENAME]
    seen: set[Path] = set()
    for name in known:
        path = directory / name
        if path.is_file():
            info = analyze_log_file(path)
            report.files.append(info)
            report.total_errors += info.errors
            report.total_warnings += info.warnings
            seen.add(path.resolve())

    for path in list_log_files(log_dir=directory):
        if path.resolve() in seen:
            continue
        info = analyze_log_file(path)
        report.files.append(info)
        report.total_errors += info.errors
        report.total_warnings += info.warnings

    report.sessions = parse_recent_sessions(log_dir=directory)
    return report


def resolve_named_log_file(name: str, *, log_dir: Path | None = None) -> Path | None:
    """Resolve shorthand log names: errors, database, backup, main."""
    directory = log_dir or resolve_log_dir()
    normalized = name.strip().lower()
    mapping = {
        "error": ERROR_LOG_FILENAME,
        "errors": ERROR_LOG_FILENAME,
        "database": DATABASE_LOG_FILENAME,
        "db": DATABASE_LOG_FILENAME,
        "backup": BACKUP_LOG_FILENAME,
        "backups": BACKUP_LOG_FILENAME,
    }
    if normalized in mapping:
        path = directory / mapping[normalized]
        return path if path.is_file() else path
    if normalized == "main":
        files = list_log_files(log_dir=directory)
        return files[0] if files else None
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else candidate
    return directory / name

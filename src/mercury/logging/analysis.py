"""Log file analysis — status, sessions, and counts."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from mercury.logging.config import (
    BACKUP_LOG_FILENAME,
    DATABASE_LOG_FILENAME,
    ERROR_LOG_FILENAME,
    logging_enabled,
)
from mercury.logging.engine import active_log_directory, current_log_file, is_configured
from mercury.logging.utils import list_log_files

_LOG_RECORD = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z? "
    r"\[(?P<id>[^\]]+)\] (?P<level>[A-Z]+) (?P<logger>\S+) (?P<message>.*)$"
)
SESSION_START_RE = re.compile(r"^session start id=(\S+)")
SESSION_END_RE = re.compile(r"^session end id=(\S+) exit_code=(-?\d+)(?: elapsed_ms=(\d+(?:\.\d+)?))?")


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
    elapsed_ms: float | None = None
    ambiguous: bool = False


@dataclass
class LogStatusReport:
    log_dir: Path
    logging_enabled: bool
    files: list[LogFileInfo] = field(default_factory=list)
    sessions: list[LogSession] = field(default_factory=list)
    total_errors: int = 0
    total_warnings: int = 0


def analyze_log_file(path: Path) -> LogFileInfo:
    lines = errors = warnings = 0
    size_bytes = 0
    modified = ""
    try:
        info = path.stat()
        size_bytes = info.st_size
        modified = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc).isoformat()
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines += 1
                record = _LOG_RECORD.match(line)
                if record:
                    level = record["level"]
                    errors += level in ("ERROR", "CRITICAL")
                    warnings += level == "WARNING"
    except OSError:
        pass
    return LogFileInfo(
        name=path.name,
        path=path,
        size_bytes=size_bytes,
        lines=lines,
        errors=errors,
        warnings=warnings,
        modified_at=modified,
    )


def parse_recent_sessions(*, log_dir: Path | None = None, max_sessions: int = 10) -> list[LogSession]:
    """Extract recent session start/end pairs from main daily logs."""
    if max_sessions <= 0:
        return []
    directory = log_dir or active_log_directory()
    pending: dict[str, deque[int]] = {}
    sessions: list[LogSession] = []

    for path in reversed(list_log_files(log_dir=directory)):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    record = _LOG_RECORD.match(line)
                    if not record or record["logger"] != "mercury":
                        continue
                    sid = record["id"]
                    message = record["message"]
                    start = SESSION_START_RE.match(message)
                    if start and start[1] == sid:
                        indexes = pending.setdefault(sid, deque())
                        for index in indexes:
                            sessions[index] = replace(sessions[index], ambiguous=True)
                        sessions.append(LogSession(
                            sid, line.strip(), command=_extract_command(message), ambiguous=bool(indexes)
                        ))
                        indexes.append(len(sessions) - 1)
                        continue
                    end = SESSION_END_RE.match(message)
                    if not end or end[1] != sid:
                        continue
                    elapsed = float(end[3]) if end[3] is not None else None
                    indexes = pending.get(sid)
                    if indexes:
                        index = indexes.popleft()
                        if not indexes:
                            del pending[sid]
                        session = sessions[index]
                        if not session.ambiguous:
                            sessions[index] = replace(
                                session, end_line=line.strip(), exit_code=int(end[2]), elapsed_ms=elapsed
                            )
                            continue
                        sessions.append(LogSession(
                            sid, "", end_line=line.strip(), exit_code=int(end[2]),
                            elapsed_ms=elapsed, ambiguous=True,
                        ))
                    else:
                        sessions.append(LogSession(sid, "", line.strip(), int(end[2]), elapsed_ms=elapsed))
        except OSError:
            continue

    sessions.reverse()
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
    directory = log_dir or active_log_directory()
    enabled = current_log_file() is not None if is_configured() else logging_enabled()
    report = LogStatusReport(log_dir=directory, logging_enabled=enabled)

    if not directory.is_dir():
        return report

    known = [ERROR_LOG_FILENAME, DATABASE_LOG_FILENAME, BACKUP_LOG_FILENAME]
    seen: set[Path] = set()
    for name in known:
        path = directory / name
        if path.is_file():
            info = analyze_log_file(path)
            report.files.append(info)
            seen.add(path.resolve())

    for path in list_log_files(log_dir=directory):
        if path.resolve() in seen:
            continue
        info = analyze_log_file(path)
        report.files.append(info)
        # The dedicated logs mirror daily records. Totals count the daily stream.
        report.total_errors += info.errors
        report.total_warnings += info.warnings

    report.sessions = parse_recent_sessions(log_dir=directory)
    return report


def resolve_named_log_file(name: str, *, log_dir: Path | None = None) -> Path | None:
    """Resolve shorthand log names: errors, database, backup, main."""
    directory = log_dir or active_log_directory()
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
        return directory / mapping[normalized]
    if normalized == "main":
        files = list_log_files(log_dir=directory)
        return files[0] if files else None
    return directory / name

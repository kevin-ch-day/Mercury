"""Content-level safety checks for recoverable MariaDB logical backups.

Checks are deliberately based on the logical dump text rather than a backup
tool exit status.  A dump tool can exit successfully while omitting object
classes unless its option contract and emitted content are both verified.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.session import readonly_scalars

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$]+$")
_VERSION_COMMENT = re.compile(r"/\*!\d{5}\s*")
_CREATE_OBJECT_RE = re.compile(
    r"^\s*CREATE\b(?:\s+OR\s+REPLACE\b)?"
    r"(?:\s+ALGORITHM\s*=\s*\w+)?"
    r"(?:\s+DEFINER\s*=\s*(?:`[^`]+`@`[^`]+`|\S+))?"
    r"(?:\s+SQL\s+SECURITY\s+(?:DEFINER|INVOKER))?"
    r"\s+(TABLE|VIEW|TRIGGER|PROCEDURE|FUNCTION|EVENT)\b",
    re.IGNORECASE,
)


class BackupObjectInventory(BaseModel):
    """Named recoverability-relevant objects in one database."""

    tables: list[str] = Field(default_factory=list)
    views: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "tables": len(self.tables),
            "views": len(self.views),
            "triggers": len(self.triggers),
            "procedures": len(self.procedures),
            "functions": len(self.functions),
            "events": len(self.events),
        }


class BackupContentContract(BaseModel):
    """Live-versus-dump object inventory attached to a successful backup."""

    live: BackupObjectInventory
    dump: BackupObjectInventory
    schema_dump: BackupObjectInventory | None = None
    verified: bool = False
    issues: list[str] = Field(default_factory=list)


def _quote_schema(database: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(database):
        raise ValueError(f"Unsafe database identifier: {database!r}")
    return database.replace("'", "''")


def fetch_live_object_inventory(
    config: MariaDbConnectionConfig,
    database: str,
    *,
    scalars: Callable[[MariaDbConnectionConfig, str], list[str]] = readonly_scalars,
) -> BackupObjectInventory:
    """Read names of all object classes that a logical dump must preserve."""
    schema = _quote_schema(database)

    def query(sql: str) -> list[str]:
        return sorted(set(scalars(config, sql)))

    return BackupObjectInventory(
        tables=query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_TYPE = 'BASE TABLE'"
        ),
        views=query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_TYPE = 'VIEW'"
        ),
        triggers=query(
            "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
            f"WHERE TRIGGER_SCHEMA = '{schema}'"
        ),
        procedures=query(
            "SELECT ROUTINE_NAME FROM information_schema.ROUTINES "
            f"WHERE ROUTINE_SCHEMA = '{schema}' AND ROUTINE_TYPE = 'PROCEDURE'"
        ),
        functions=query(
            "SELECT ROUTINE_NAME FROM information_schema.ROUTINES "
            f"WHERE ROUTINE_SCHEMA = '{schema}' AND ROUTINE_TYPE = 'FUNCTION'"
        ),
        events=query(
            "SELECT EVENT_NAME FROM information_schema.EVENTS "
            f"WHERE EVENT_SCHEMA = '{schema}'"
        ),
    )


def _decomment(statement: str) -> str:
    """Normalize mysqldump/MariaDB conditional-comment wrappers on one line."""
    return _VERSION_COMMENT.sub("", statement).replace("*/", "")


def _name_after(statement: str, token: str) -> str | None:
    pattern = re.compile(
        rf"\b{token}\b\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:(?:`[^`]+`|[A-Za-z0-9_$]+)\.)?`?([A-Za-z0-9_$]+)`?",
        re.IGNORECASE,
    )
    match = pattern.search(statement)
    return match.group(1) if match else None


def extract_dump_object_inventory(path: Path) -> BackupObjectInventory:
    """Extract recoverability object names from a gzip logical dump.

    MariaDB emits object declarations on individual lines, including inside
    versioned conditional comments.  This streaming parser intentionally does
    not load a large production dump into memory.
    """
    found: dict[str, set[str]] = {
        "tables": set(), "views": set(), "triggers": set(),
        "procedures": set(), "functions": set(), "events": set(),
    }
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = _decomment(raw_line)
            # Do not treat a SQL comment, view body, or application payload
            # containing words such as "create procedure" as an object
            # declaration.  MariaDB's own conditional-comment declarations
            # normalize to a line beginning with CREATE above.
            declaration = _CREATE_OBJECT_RE.match(line)
            if declaration is None:
                continue
            object_type = declaration.group(1).casefold()
            key = {
                "table": "tables",
                "view": "views",
                "trigger": "triggers",
                "procedure": "procedures",
                "function": "functions",
                "event": "events",
            }[object_type]
            name = _name_after(line, object_type)
            if name:
                found[key].add(name)
    return BackupObjectInventory(**{key: sorted(values) for key, values in found.items()})


def compare_object_inventories(
    live: BackupObjectInventory,
    dumped: BackupObjectInventory,
) -> list[str]:
    """Return exact object-set mismatches; an empty list is a passing contract."""
    issues: list[str] = []
    for field in ("tables", "views", "triggers", "procedures", "functions", "events"):
        expected = set(getattr(live, field))
        actual = set(getattr(dumped, field))
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing:
            issues.append(f"{field}: dump missing {', '.join(missing)}")
        if unexpected:
            issues.append(f"{field}: dump has unexpected {', '.join(unexpected)}")
    return issues


def build_backup_content_contract(
    live: BackupObjectInventory,
    dump: BackupObjectInventory,
    schema_dump: BackupObjectInventory | None = None,
) -> BackupContentContract:
    """Require the full dump, and when present its schema companion, to match live."""
    issues = compare_object_inventories(live, dump)
    if schema_dump is not None:
        issues.extend(
            f"schema companion: {issue}"
            for issue in compare_object_inventories(live, schema_dump)
        )
    return BackupContentContract(
        live=live,
        dump=dump,
        schema_dump=schema_dump,
        verified=not issues,
        issues=issues,
    )

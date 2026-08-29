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

from mercury.backup.checksum import sha256_file

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
_RESTORE_OBJECT_STATEMENT_RE = re.compile(
    r"^\s*(DROP|CREATE|ALTER|LOCK|UNLOCK)\s+"
    r"(?:OR\s+REPLACE\s+)?(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?:TEMPORARY\s+)?"
    r"(?:ALGORITHM\s*=\s*\w+\s+)?"
    r"(?:DEFINER\s*=\s*(?:`[^`]+`@`[^`]+`|\S+)\s+)?"
    r"(?:SQL\s+SECURITY\s+(?:DEFINER|INVOKER)\s+)?"
    r"(DATABASE|TABLE|VIEW|TRIGGER|PROCEDURE|FUNCTION|EVENT|INDEX|TABLES)\b",
    re.IGNORECASE,
)
_RESTORE_DATA_STATEMENT_RE = re.compile(r"^\s*(INSERT|REPLACE)\s+(?:INTO\s+)?", re.IGNORECASE)
_MARIADB_VIEW_ALGORITHM_PREFIX_RE = re.compile(
    r"^CREATE\s+ALGORITHM\s*=\s*\w+$", re.IGNORECASE
)
_MARIADB_VIEW_SECURITY_PREFIX_RE = re.compile(
    r"^(?:DEFINER\s*=\s*(?:`[^`]+`@`[^`]+`|\S+)\s+)?"
    r"SQL\s+SECURITY\s+(?:DEFINER|INVOKER)$",
    re.IGNORECASE,
)
_MARIADB_VIEW_BODY_RE = re.compile(r"^VIEW\b", re.IGNORECASE)
_UNKNOWN_OBJECT_DDL_RE = re.compile(r"^\s*(DROP|CREATE|ALTER)\b", re.IGNORECASE)
_PRIVILEGED_TOP_LEVEL_RE = re.compile(
    r"^\s*(GRANT|REVOKE|INSTALL|UNINSTALL|FLUSH|SHUTDOWN|KILL|"
    r"(?:CREATE|ALTER|DROP)\s+(?:USER|ROLE|SERVER)|SET\s+(?:GLOBAL|PERSIST)|"
    r"LOAD\s+(?:DATA|XML|FILE)|LOCK\s+INSTANCE)\b",
    re.IGNORECASE,
)
# Qualified column references (``alias`.`column``) are common in view SQL and
# cannot be distinguished from schema references by punctuation alone.  Limit
# extraction to FROM/JOIN relation positions, where MariaDB names a relation.
_QUOTED_SCHEMA_REFERENCE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+`([^`]+)`\s*\.\s*`[^`]+`", re.IGNORECASE
)
_SYSTEM_SCHEMAS = frozenset({"information_schema", "performance_schema", "mysql", "sys"})
RESTORE_REQUIREMENTS_CONTRACT_VERSION = 1
IMPORT_TRANSFORM_VERSION = "2"
RESTORE_REQUIREMENTS_SCANNER_VERSION = "5"


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


class RestoreRequirementsContract(BaseModel):
    """Executable statement families required by one exact logical artifact."""

    contract_version: int = RESTORE_REQUIREMENTS_CONTRACT_VERSION
    scanner_version: str = RESTORE_REQUIREMENTS_SCANNER_VERSION
    import_transform_version: str = IMPORT_TRANSFORM_VERSION
    artifact_file: str
    artifact_sha256: str
    statement_classes: list[str] = Field(default_factory=list)
    # Schemas named by qualified identifiers other than the source schema.
    # A restore identity needs an explicitly approved read capability for these
    # dependencies when it creates views/routines in the dev target.
    external_schema_references: list[str] = Field(default_factory=list)
    unknown_privileged_statements: list[str] = Field(default_factory=list)
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


def extract_restore_requirements(
    path: Path, *, source_database: str | None = None
) -> RestoreRequirementsContract:
    """Stream an exact dump and record executable restore statement families.

    MariaDB dumps put top-level DDL/DML on their own lines, including versioned
    executable comments.  We deliberately only fail closed for unknown DDL or
    privileged/destructive statements; routine bodies and ordinary data values
    are not independently scanned as operations.
    """
    statements: set[str] = set()
    unknown: set[str] = set()
    external_schemas: set[str] = set()
    source_schema = source_database or re.sub(
        r"_\d{8}_\d{6}_\d+(?:\.schema)?\.sql\.gz$", "", path.name
    )
    pending_view_algorithm: str | None = None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = _decomment(raw_line).strip()
            if not line or line.startswith("--") or line.startswith("#"):
                continue
            # A conditional comment is commonly followed by the statement
            # terminator (``*/;``); it is irrelevant to classification but
            # retain the original text for fail-closed diagnostic evidence.
            sql_line = line.rstrip(";").rstrip()
            # Inspect only executable schema/object declarations.  This keeps
            # application strings in bulk INSERT data out of the dependency
            # contract while covering view/routine relation dependencies.
            if re.match(r"^(?:CREATE|ALTER|DROP|VIEW)\b", sql_line, re.IGNORECASE):
                for match in _QUOTED_SCHEMA_REFERENCE_RE.finditer(sql_line):
                    schema = match.group(1)
                    if schema and schema != source_schema and schema not in _SYSTEM_SCHEMAS:
                        external_schemas.add(schema)
            # mariadb-dump writes final view definitions as three executable
            # comments: CREATE ALGORITHM, DEFINER/SQL SECURITY, then VIEW.
            # Treat that exact sequence as one CREATE VIEW statement.  Do not
            # suppress a standalone/unfinished CREATE ALGORITHM declaration.
            if pending_view_algorithm is not None:
                if _MARIADB_VIEW_SECURITY_PREFIX_RE.match(sql_line):
                    continue
                if _MARIADB_VIEW_BODY_RE.match(sql_line):
                    statements.add("CREATE VIEW")
                    pending_view_algorithm = None
                    continue
                unknown.add(pending_view_algorithm)
                pending_view_algorithm = None
            if _MARIADB_VIEW_ALGORITHM_PREFIX_RE.match(sql_line):
                pending_view_algorithm = sql_line
                continue
            match = _RESTORE_OBJECT_STATEMENT_RE.match(sql_line)
            if match:
                statements.add(f"{match.group(1).upper()} {match.group(2).upper()}")
                continue
            data_match = _RESTORE_DATA_STATEMENT_RE.match(sql_line)
            if data_match:
                statements.add(data_match.group(1).upper())
                continue
            # Unknown CREATE/DROP/ALTER forms can change the required
            # privileges.  They must not be silently authorized for a live
            # destructive reset.  Explicitly privileged server statements are
            # likewise not part of Mercury's ordinary dev restore contract.
            if _UNKNOWN_OBJECT_DDL_RE.match(sql_line) or _PRIVILEGED_TOP_LEVEL_RE.match(sql_line):
                unknown.add(re.sub(r"\s+", " ", line[:240]))
    if pending_view_algorithm is not None:
        unknown.add(pending_view_algorithm)
    return RestoreRequirementsContract(
        artifact_file=path.name,
        artifact_sha256=sha256_file(path),
        statement_classes=sorted(statements),
        external_schema_references=sorted(external_schemas),
        unknown_privileged_statements=sorted(unknown),
        verified=True,
    )


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

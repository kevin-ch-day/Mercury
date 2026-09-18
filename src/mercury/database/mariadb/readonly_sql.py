"""Fail-closed allowlist for live read-only MariaDB helpers."""

from __future__ import annotations

import re

from mercury.database.mariadb.errors import MariaDbLiveError

_READONLY_START = re.compile(r"^(?:SHOW|SELECT|EXPLAIN)\b", re.IGNORECASE)
_COMMENTS = re.compile(r"(?:--|/\*|\*/|#)")
_WRITE_KEYWORDS = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|TRUNCATE|CALL|GRANT|REVOKE|"
    r"LOAD\s+DATA|INTO\s+OUTFILE|FOR\s+UPDATE|LOCK\s+TABLES|SET)\b",
    re.IGNORECASE,
)
_DDL_KEYWORDS = re.compile(r"\b(?:CREATE|ALTER|DROP)\b", re.IGNORECASE)


def assert_live_readonly_sql(sql: str) -> None:
    """Reject write/DDL SQL on the live read-only query path.

    ``SHOW CREATE`` / ``SHOW GRANTS`` remain allowed because they start with
    SHOW. Multi-statement scripts are checked one statement at a time.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise MariaDbLiveError("Read-only SQL must be a non-empty statement.")
    for statement in sql.split(";"):
        text = statement.strip()
        if not text:
            continue
        if _COMMENTS.search(text):
            raise MariaDbLiveError("Read-only SQL must not contain SQL comments.")
        if not _READONLY_START.match(text):
            raise MariaDbLiveError(
                "Read-only SQL must begin with SHOW, SELECT, or EXPLAIN."
            )
        if text.upper().startswith("SHOW"):
            continue
        if _WRITE_KEYWORDS.search(text) or _DDL_KEYWORDS.search(text):
            raise MariaDbLiveError("Read-only SQL contains a write-capable keyword.")

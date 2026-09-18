"""Fail-closed quoting for MariaDB identifiers interpolated into SQL."""

from __future__ import annotations

import re

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$]+$")


def assert_safe_identifier(name: str, *, what: str = "identifier") -> str:
    if not isinstance(name, str) or not SAFE_IDENTIFIER.fullmatch(name):
        raise ValueError(f"Unsafe SQL {what}: {name!r}")
    return name


def quote_ident(name: str, *, what: str = "identifier") -> str:
    return f"`{assert_safe_identifier(name, what=what)}`"


def sql_schema_literal(name: str, *, what: str = "schema") -> str:
    """Schema name safe to interpolate into a single-quoted SQL literal."""
    return assert_safe_identifier(name, what=what)

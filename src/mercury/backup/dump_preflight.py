"""Read-only dumpability checks before mariadb-dump.

A dump tool can fail on SHOW CREATE TABLE for a view that is already broken
at query time (for example a collation mix baked into the view).  Mercury
must refuse that source rather than skip the view or accept a partial dump.

SHOW CREATE probes use the mariadb CLI (same client family as mariadb-dump),
not pymysql. pymysql charset=utf8mb4 often uses utf8mb4_general_ci while
MariaDB 11.8 clients default to utf8mb4_uca1400_ai_ci, so a pymysql probe
can pass and the dump can still fail.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.errors import MariaDbLiveError

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$]+$")
_SHOW_CREATE_NAME_RE = re.compile(
    r"show create (?:table|view)\s+(?:`[^`]+`\s*\.\s*)?`?([A-Za-z0-9_$]+)`?",
    re.IGNORECASE,
)
_COLLATION_MIX_RE = re.compile(
    r"Illegal mix of collations \(([^,)]+)(?:,[^)]*)?\) and \(([^,)]+)",
    re.IGNORECASE,
)
_FORCE_SHOW_ERROR_RE = re.compile(
    r"SHOW CREATE TABLE\s+`([^`]+)`\.`([^`]+)`\s*\n-+\s*\n+\s*ERROR[^\n]*:\s*(.+)",
    re.IGNORECASE,
)
_FORCE_LINE_ERROR_RE = re.compile(
    r"^ERROR\b[^\n]*\bat line\s+(\d+):\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_SQL_ERROR_LINE_RE = re.compile(r"^ERROR\b", re.MULTILINE | re.IGNORECASE)

ShowCreateFn = Callable[[MariaDbConnectionConfig, str], None]

_REPAIR_HINT = (
    "Recreate the view(s) in the source schema so SHOW CREATE TABLE succeeds; "
    "Mercury will not skip views or accept a partial dump."
)


def owning_project(database: str) -> str | None:
    """Catalog project that owns this backup source, if known."""
    from mercury.database.core.catalog import CATALOG_BY_NAME

    entry = CATALOG_BY_NAME.get(database)
    return entry.project if entry else None


def repair_hint(database: str | None = None) -> str:
    """Where the operator must recreate broken views (never inside Mercury)."""
    project = owning_project(database) if database else None
    if project:
        return (
            f"Recreate the view(s) in {project} so SHOW CREATE TABLE succeeds; "
            "Mercury will not skip views or accept a partial dump."
        )
    return _REPAIR_HINT


class DatabaseDumpability(BaseModel):
    """Read-only dumpability result for one backup source."""

    database: str
    view_count: int = 0
    issues: list[str] = Field(default_factory=list)
    error: str | None = None

    @property
    def dumpable(self) -> bool:
        return not self.issues and self.error is None

    def refusal_text(self) -> str | None:
        """Operator-facing refusal for this source, or None when dumpable."""
        if self.dumpable:
            return None
        if self.error:
            return (
                f"dumpability inspect failed: {_compact_detail(self.error)}. "
                f"{repair_hint(self.database)}"
            )
        hint = repair_hint(self.database)
        if len(self.issues) == 1:
            return f"{self.issues[0]}. {hint}"
        return (
            f"{len(self.issues)} views are not dumpable: "
            f"{'; '.join(self.issues)}. {hint}"
        )


class SourcesDumpability(BaseModel):
    """Dumpability for a set of backup sources."""

    entries: list[DatabaseDumpability] = Field(default_factory=list)

    @property
    def blocked(self) -> list[DatabaseDumpability]:
        return [entry for entry in self.entries if not entry.dumpable]

    def blocked_names(self) -> set[str]:
        return {entry.database for entry in self.blocked}

    @property
    def dumpable_count(self) -> int:
        return sum(1 for entry in self.entries if entry.dumpable)

    def issue_lines(self) -> list[str]:
        lines: list[str] = []
        for entry in self.blocked:
            if entry.error:
                lines.append(
                    f"{entry.database}: dumpability inspect failed: "
                    f"{_compact_detail(entry.error)}"
                )
            for issue in entry.issues:
                lines.append(f"{entry.database}: {issue}")
        return lines

    def operator_repair_hint(self) -> str:
        blocked = self.blocked
        if not blocked:
            return _REPAIR_HINT
        projects = {owning_project(entry.database) for entry in blocked}
        projects.discard(None)
        if len(projects) == 1:
            return repair_hint(blocked[0].database)
        return _REPAIR_HINT


_VIEW_BACKTICK_RE = re.compile(r"view `([^`]+)`")


def undumpable_view_names(texts: Sequence[str]) -> list[str]:
    """Unique view names mentioned in dumpability refusals, in first-seen order."""
    names: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _VIEW_BACKTICK_RE.finditer(text or ""):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def format_undumpable_next_action(
    databases: Sequence[str], errors: Sequence[str]
) -> str:
    """One Next line after a production dump that failed closed on views."""
    projects = sorted({owning_project(name) or "" for name in databases} - {""})
    views = undumpable_view_names(errors)
    if views:
        quoted = ", ".join(f"`{name}`" for name in views)
        view_label = f"view {quoted}" if len(views) == 1 else f"views {quoted}"
    else:
        view_label = "view(s)"
    owner = projects[0] if len(projects) == 1 else "source"
    return (
        f"Recreate undumpable {owner} {view_label}, "
        "then rerun Backup production."
    )


def quote_ident(name: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f"`{name}`"


def collation_mix_pair(detail: str) -> tuple[str, str] | None:
    match = _COLLATION_MIX_RE.search(detail or "")
    if match is None:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _compact_detail(detail: str, *, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", (detail or "").strip())
    if len(compact) > limit:
        return compact[: limit - 3] + "..."
    return compact


def describe_view_dump_failure(view: str, detail: str) -> str:
    """One-line diagnosis for a view that cannot be dumped."""
    pair = collation_mix_pair(detail)
    if pair is not None:
        return (
            f"view `{view}` is not dumpable "
            f"(collation mix {pair[0]} vs {pair[1]})"
        )
    compact = _compact_detail(detail)
    if compact:
        return f"view `{view}` is not dumpable: {compact}"
    return f"view `{view}` is not dumpable"


def classify_dump_tool_error(
    detail: str, *, database: str | None = None
) -> str:
    """Rewrite mariadb-dump stderr when the failure is a known source defect."""
    text = (detail or "").strip()
    if not text:
        return text
    hint = repair_hint(database)
    view_match = _SHOW_CREATE_NAME_RE.search(text)
    if view_match is None:
        if collation_mix_pair(text) is None:
            return text
        return f"{describe_view_dump_failure('unknown', text)}. {hint}"
    diagnosed = describe_view_dump_failure(view_match.group(1), text)
    if collation_mix_pair(text) is not None or "1267" in text:
        return f"{diagnosed}. {hint}"
    return diagnosed


def _default_show_create(config: MariaDbConnectionConfig, sql: str) -> None:
    # mariadb-dump uses the CLI's default collation (MariaDB 11.8:
    # utf8mb4_uca1400_ai_ci). pymysql charset=utf8mb4 often uses
    # utf8mb4_general_ci, so SHOW CREATE can pass in preflight and then fail
    # in the dump. Probe with the same client family as the dump tool.
    from mercury.database.mariadb.client import run_client_query

    run_client_query(config, sql)


def parse_force_show_create_errors(stderr: str) -> dict[str, str]:
    """Map view name → ERROR text from ``mariadb --force`` SHOW CREATE stderr."""
    errors: dict[str, str] = {}
    for match in _FORCE_SHOW_ERROR_RE.finditer(stderr or ""):
        errors[match.group(2)] = match.group(3).strip()
    return errors


def parse_force_show_create_errors_qualified(
    stderr: str,
) -> dict[tuple[str, str], str]:
    """Map (schema, view) → ERROR text from ``mariadb --force`` stderr."""
    errors: dict[tuple[str, str], str] = {}
    for match in _FORCE_SHOW_ERROR_RE.finditer(stderr or ""):
        errors[(match.group(1), match.group(2))] = match.group(3).strip()
    return errors


def parse_force_errors_by_line(stderr: str) -> dict[int, str]:
    """Map 1-based script line → ERROR text (clients that omit the SQL echo)."""
    errors: dict[int, str] = {}
    for match in _FORCE_LINE_ERROR_RE.finditer(stderr or ""):
        errors[int(match.group(1))] = match.group(2).strip()
    return errors


def stderr_has_sql_error(stderr: str) -> bool:
    return _SQL_ERROR_LINE_RE.search(stderr or "") is not None


def resolve_force_show_create_errors(
    stderr: str, views: Sequence[str]
) -> dict[str, str]:
    """Attribute ``--force`` errors to view names (SQL echo, then line number)."""
    named = parse_force_show_create_errors(stderr)
    merged = dict(named)
    for line_no, message in parse_force_errors_by_line(stderr).items():
        idx = line_no - 1
        if 0 <= idx < len(views) and views[idx] not in merged:
            merged[views[idx]] = message
    return merged


def resolve_force_show_create_errors_qualified(
    stderr: str, pairs: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """Attribute ``--force`` errors to (schema, view) pairs."""
    named = parse_force_show_create_errors_qualified(stderr)
    merged = dict(named)
    for line_no, message in parse_force_errors_by_line(stderr).items():
        idx = line_no - 1
        if 0 <= idx < len(pairs) and pairs[idx] not in merged:
            merged[pairs[idx]] = message
    return merged


def _client_force_script(
    config: MariaDbConnectionConfig, sql_script: str
) -> tuple[int, str, str]:
    from mercury.database.mariadb.client import run_client_force_script

    return run_client_force_script(
        config,
        sql_script,
        timeout=max(int(config.connect_timeout or 10), 60),
    )


def _batch_view_dumpability_issues(
    config: MariaDbConnectionConfig, database: str, views: list[str]
) -> list[str]:
    schema = quote_ident(database)
    statements: list[str] = []
    for view in views:
        statements.append(f"SHOW CREATE TABLE {schema}.{quote_ident(view)};")
    # mariadb --force returns 0 when some statements fail, so exit code alone
    # cannot prove dumpability. Attribute ERROR lines, then fail closed.
    _code, _stdout, stderr = _client_force_script(config, "\n".join(statements))
    parsed = resolve_force_show_create_errors(stderr, views)
    if not parsed and (stderr_has_sql_error(stderr) or _code != 0):
        raise MariaDbLiveError(_compact_detail(stderr) or "dumpability probe failed")
    return [
        describe_view_dump_failure(view, parsed[view])
        for view in views
        if view in parsed
    ]


def view_dumpability_issues(
    config: MariaDbConnectionConfig,
    database: str,
    views: list[str],
    *,
    show_create: ShowCreateFn | None = None,
) -> list[str]:
    """Return diagnoses for views that cannot SHOW CREATE TABLE (read-only)."""
    if not views:
        return []
    try:
        schema = quote_ident(database)
    except ValueError:
        return [f"database {database!r} is not a safe identifier"]
    safe_views: list[str] = []
    issues: list[str] = []
    for view in views:
        try:
            quote_ident(view)
        except ValueError:
            issues.append(f"view {view!r} is not a safe identifier")
            continue
        safe_views.append(view)
    if show_create is None and safe_views:
        try:
            issues.extend(_batch_view_dumpability_issues(config, database, safe_views))
            return issues
        except MariaDbLiveError:
            # Fall back to one SHOW CREATE per view so a batch-parser miss
            # still fails closed on the actual undumpable object.
            pass
    runner = show_create or _default_show_create
    for view in safe_views:
        sql = f"SHOW CREATE TABLE {schema}.{quote_ident(view)}"
        try:
            runner(config, sql)
        except MariaDbLiveError as exc:
            issues.append(describe_view_dump_failure(view, str(exc)))
        except Exception as exc:  # noqa: BLE001 — fail closed; dump cannot proceed
            issues.append(describe_view_dump_failure(view, str(exc)))
    return issues


def view_dumpability_error(
    config: MariaDbConnectionConfig,
    database: str,
    views: list[str],
    *,
    show_create: ShowCreateFn | None = None,
) -> str | None:
    """Operator-facing refusal when any listed view cannot be dumped."""
    if not views:
        return None
    issues = view_dumpability_issues(
        config, database, views, show_create=show_create
    )
    return DatabaseDumpability(
        database=database, view_count=len(views), issues=issues
    ).refusal_text()


def fetch_live_view_names(
    config: MariaDbConnectionConfig,
    database: str,
    *,
    scalars: Callable[[MariaDbConnectionConfig, str], list[str]] | None = None,
) -> list[str]:
    """Read-only view names for dumpability (information_schema only)."""
    if not _SAFE_IDENTIFIER.fullmatch(database):
        raise ValueError(f"Unsafe database identifier: {database!r}")
    from mercury.database.mariadb.session import readonly_scalars

    query = (
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_TYPE = 'VIEW'"
    )
    runner = scalars or readonly_scalars
    return sorted(set(runner(config, query)))


def fetch_live_view_names_by_database(
    config: MariaDbConnectionConfig,
    databases: Sequence[str],
    *,
    query_fn: Callable[[MariaDbConnectionConfig, str], str] | None = None,
) -> dict[str, list[str]]:
    """Read-only view names for many schemas in one information_schema query."""
    safe = [name for name in databases if _SAFE_IDENTIFIER.fullmatch(name)]
    result: dict[str, list[str]] = {name: [] for name in safe}
    if not safe:
        return result
    in_list = ",".join(f"'{name}'" for name in safe)
    sql = (
        "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({in_list}) AND TABLE_TYPE = 'VIEW'"
    )
    if query_fn is not None:
        raw = query_fn(config, sql)
    else:
        from mercury.database.mariadb.client import run_client_query

        raw = run_client_query(config, sql)
    for line in (raw or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and parts[0] in result:
            result[parts[0]].append(parts[1])
    for name in result:
        result[name] = sorted(set(result[name]))
    return result


def _assess_sources_with_one_force_script(
    config: MariaDbConnectionConfig, sources: Sequence[str]
) -> list[DatabaseDumpability]:
    """One information_schema query + one ``mariadb --force`` for all sources."""
    unsafe: dict[str, DatabaseDumpability] = {}
    safe_sources: list[str] = []
    for name in sources:
        if not _SAFE_IDENTIFIER.fullmatch(name):
            unsafe[name] = DatabaseDumpability(
                database=name,
                error=f"database {name!r} is not a safe identifier",
            )
        else:
            safe_sources.append(name)
    views_by_db = fetch_live_view_names_by_database(config, safe_sources)
    pairs: list[tuple[str, str]] = []
    unsafe_view_issues: dict[str, list[str]] = {name: [] for name in safe_sources}
    for database in safe_sources:
        for view in views_by_db.get(database, []):
            if not _SAFE_IDENTIFIER.fullmatch(view):
                unsafe_view_issues[database].append(f"view {view!r} is not a safe identifier")
                continue
            pairs.append((database, view))
    parsed: dict[tuple[str, str], str] = {}
    if pairs:
        statements = [
            f"SHOW CREATE TABLE {quote_ident(database)}.{quote_ident(view)};"
            for database, view in pairs
        ]
        code, _stdout, stderr = _client_force_script(config, "\n".join(statements))
        parsed = resolve_force_show_create_errors_qualified(stderr, pairs)
        if not parsed and (stderr_has_sql_error(stderr) or code != 0):
            raise MariaDbLiveError(
                _compact_detail(stderr) or "dumpability probe failed"
            )
    by_name: dict[str, DatabaseDumpability] = {}
    for database in safe_sources:
        issues = list(unsafe_view_issues.get(database, []))
        for view in views_by_db.get(database, []):
            key = (database, view)
            if key in parsed:
                issues.append(describe_view_dump_failure(view, parsed[key]))
        by_name[database] = DatabaseDumpability(
            database=database,
            view_count=len(views_by_db.get(database, [])),
            issues=issues,
        )
    return [unsafe.get(name) or by_name[name] for name in sources]


def assess_database_dumpability(
    config: MariaDbConnectionConfig,
    database: str,
    *,
    view_names: list[str] | None = None,
    show_create: ShowCreateFn | None = None,
) -> DatabaseDumpability:
    """Inspect one source's views for mariadb-dump SHOW CREATE failures."""
    try:
        views = (
            list(view_names)
            if view_names is not None
            else fetch_live_view_names(config, database)
        )
    except Exception as exc:  # noqa: BLE001 — inspect failure is not dumpable
        return DatabaseDumpability(database=database, error=str(exc))
    issues = view_dumpability_issues(
        config, database, views, show_create=show_create
    )
    return DatabaseDumpability(
        database=database, view_count=len(views), issues=issues
    )


def try_assess_sources_dumpability(
    sources: Sequence[str],
    *,
    config: MariaDbConnectionConfig | None = None,
    assess_one: Callable[[MariaDbConnectionConfig, str], DatabaseDumpability] | None = None,
) -> SourcesDumpability:
    """Best-effort dumpability for backup sources; empty when offline."""
    from mercury.database.mariadb.session import try_load_mariadb_config

    cfg = config if config is not None else try_load_mariadb_config()
    if cfg is None:
        return SourcesDumpability()
    if assess_one is not None:
        entries: list[DatabaseDumpability] = []
        for name in sources:
            try:
                entries.append(assess_one(cfg, name))
            except Exception as exc:  # noqa: BLE001 — menu/CLI must not crash
                entries.append(DatabaseDumpability(database=name, error=str(exc)))
        return SourcesDumpability(entries=entries)
    try:
        return SourcesDumpability(
            entries=_assess_sources_with_one_force_script(cfg, list(sources))
        )
    except Exception:
        entries = []
        for name in sources:
            try:
                entries.append(assess_database_dumpability(cfg, name))
            except Exception as exc:  # noqa: BLE001 — menu/CLI must not crash
                entries.append(DatabaseDumpability(database=name, error=str(exc)))
        return SourcesDumpability(entries=entries)

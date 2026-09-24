"""Read-only dedicated restore-account contract; all DCL remains DBA-owned.

Run ``python -m mercury.restore.account_contract [--pi-read-window] [--compact]``.
Raw SHOW GRANTS (which may include authentication material) is never emitted.
"""

from __future__ import annotations

import argparse
import json
import re

from mercury.sync.restore_preflight import _GRANT_RE

PRINCIPAL = "mercury_dev_restore@localhost"
TEMP_SCOPE = r"_restorecheck\_%"
DEV_SCOPES = (
    "android_permission_intel_dev",
    "erebus_threat_intel_dev",
    "scytaledroid_core_dev",
)
TEMP_RIGHTS = frozenset(
    {
        "CREATE",
        "DROP",
        "ALTER",
        "INSERT",
        "SELECT",
        "CREATE VIEW",
        "TRIGGER",
        "INDEX",
        "LOCK TABLES",
        "REFERENCES",
        "SHOW VIEW",
        "CREATE ROUTINE",
        "ALTER ROUTINE",
    }
)
DEV_RIGHTS = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "REFERENCES",
        "INDEX",
        "ALTER",
        "CREATE TEMPORARY TABLES",
        "LOCK TABLES",
        "EXECUTE",
        "CREATE VIEW",
        "SHOW VIEW",
        "CREATE ROUTINE",
        "ALTER ROUTINE",
        "EVENT",
        "TRIGGER",
        "DELETE HISTORY",
    }
)


def compact_status(result: dict) -> str:
    """Render the contract result without the large expected-rights inventory."""
    status = str(result.get("status") or "UNKNOWN")
    principal = str(result.get("current_user") or "unavailable")
    pi_window = "active" if result.get("pi_read_window") else "inactive"
    missing = result.get("missing") if isinstance(result.get("missing"), dict) else {}
    excess = result.get("excess") if isinstance(result.get("excess"), list) else []
    lines = [
        f"Restore account :: {status}",
        f"Principal       :: {principal}",
        f"PI read window  :: {pi_window}",
        f"Missing scopes  :: {len(missing)}",
        f"Excess findings :: {len(excess)}",
    ]
    for scope, rights in sorted(missing.items()):
        values = ", ".join(str(value) for value in rights)
        lines.append(f"Missing         :: {scope}: {values}")
    for finding in excess:
        lines.append(f"Excess          :: {finding}")
    detail = result.get("detail")
    if detail:
        lines.append(f"Detail          :: {detail}")
    return "\n".join(lines)


def assess_account(
    grants: list[str],
    *,
    current_user: str,
    active_roles: list[str],
    pi_read_window: bool = False,
) -> dict:
    """Recognize only reviewed scopes; never infer rights from overlapping rows.

    Missing expected grants are distinct from excess/unrecognized authority.
    Temporary PI SELECT is accepted only in an explicitly selected read window.
    Unknown role, object, routine or proxy grants fail closed without echoing SQL.
    """
    expected = {scope: set(DEV_RIGHTS) for scope in DEV_SCOPES}
    expected[TEMP_SCOPE] = set(TEMP_RIGHTS)
    if pi_read_window:
        expected["android_permission_intel"] = {"SELECT"}
    found = {scope: set() for scope in expected}
    excess = []
    if current_user != PRINCIPAL:
        excess.append("unexpected restore principal")
    if active_roles:
        excess.append("active role authority requires review")
    for line in grants:
        match = _GRANT_RE.match(line.strip())
        if not match:
            excess.append("unrecognized grant or role membership")
            continue
        rights_text, scope = match.groups()
        routine = re.fullmatch(
            r"(?:PROCEDURE|FUNCTION) `([A-Za-z0-9_]+)`\.`([A-Za-z0-9_]+)`",
            scope,
            re.IGNORECASE,
        )
        scope = scope.replace("`", "").replace("\\\\", "\\").strip()
        rights = {part.strip().upper() for part in rights_text.split(",")}
        if re.search(r"\bWITH\s+GRANT\s+OPTION\b", line, re.IGNORECASE):
            excess.append("GRANT OPTION is prohibited")
        # automatic_sp_privileges can leave creator grants after DROP DATABASE.
        # Accept only its two routine rights on disposable schemas, never PI/prod.
        if (
            routine
            and (routine[1] in DEV_SCOPES or routine[1].startswith("_restorecheck_"))
            and rights <= {"EXECUTE", "ALTER ROUTINE"}
        ):
            continue
        if scope == "*.*":
            if rights != {"USAGE"}:
                excess.append("global privileges are prohibited")
            continue
        if not scope.endswith(".*") or scope[:-2] not in expected:
            excess.append("unapproved schema/object/role scope")
            continue
        name = scope[:-2]
        # ALL on existing disposable development clones is the established policy.
        if rights == {"ALL PRIVILEGES"} and name in DEV_SCOPES:
            rights = set(DEV_RIGHTS)
        if rights - expected[name]:
            excess.append(f"excess privileges on {name}")
        found[name].update(rights & expected[name])
    missing = {
        name: sorted(rights - found[name])
        for name, rights in expected.items()
        if rights - found[name]
    }
    return {
        "contract": "mercury.restore-account.v1",
        "current_user": current_user,
        "pi_read_window": pi_read_window,
        "status": "BROADER" if excess else "MISSING" if missing else "EXACT",
        "missing": missing,
        "excess": sorted(set(excess)),
        "expected": {k: sorted(v) for k, v in expected.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pi-read-window",
        action="store_true",
        help="Expect temporary PI SELECT during an explicitly approved rehearsal",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print a short operator status instead of the complete JSON contract",
    )
    args = parser.parse_args()
    from mercury.database.mariadb.client import run_client_query
    from mercury.database.mariadb.config import load_mariadb_restore_config

    try:
        cfg = load_mariadb_restore_config()
        identity, role = (
            run_client_query(
                cfg, "SELECT CURRENT_USER(), COALESCE(CURRENT_ROLE(), 'NONE')"
            )
            .strip()
            .split("\t")
        )
        grants = run_client_query(cfg, "SHOW GRANTS").splitlines()
        result = assess_account(
            grants,
            current_user=identity,
            active_roles=[] if role == "NONE" else [role],
            pi_read_window=args.pi_read_window,
        )
    except Exception:  # noqa: BLE001 - never print connector/config secrets
        # Exceptions from connectors/configuration may contain sensitive details.
        failure = {
            "status": "INSPECTION_FAILED",
            "detail": "Cannot inspect dedicated restore account; check private configuration and connectivity.",
        }
        print(compact_status(failure) if args.compact else json.dumps(failure))
        return 2
    print(compact_status(result) if args.compact else json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "EXACT" else 1


if __name__ == "__main__":
    raise SystemExit(main())

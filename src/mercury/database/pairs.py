"""Production → development database pairs for sync planning."""

from pydantic import BaseModel

from mercury.database.core import DEV_SUFFIX, PROD_SUFFIX, DatabaseRole, classify_database

PROD_SUFFIX_LEN = len(PROD_SUFFIX)


class ProdDevPair(BaseModel):
    prod: str
    expected_dev: str
    dev: str | None = None
    dev_listed: bool = False
    project: str | None = None
    sync_notes: str = ""


def prod_to_dev_name(prod_name: str) -> str | None:
    if not prod_name.endswith(PROD_SUFFIX):
        return None
    return prod_name[: -PROD_SUFFIX_LEN] + DEV_SUFFIX


def build_prod_dev_pairs(
    database_names: list[str],
    *,
    projects: dict[str, str] | None = None,
) -> list[ProdDevPair]:
    names = set(database_names)
    projects = projects or {}
    pairs: list[ProdDevPair] = []

    for name in sorted(names):
        if not name.endswith(PROD_SUFFIX):
            continue
        expected_dev = prod_to_dev_name(name)
        if expected_dev is None:
            continue
        dev_listed = expected_dev in names
        notes = (
            "Disposable dev target; sync only after verified prod backup."
            if dev_listed
            else f"Expected dev database '{expected_dev}' not in inventory — add to config or catalog."
        )
        pairs.append(
            ProdDevPair(
                prod=name,
                expected_dev=expected_dev,
                dev=expected_dev if dev_listed else None,
                dev_listed=dev_listed,
                project=projects.get(name),
                sync_notes=notes,
            )
        )

    return pairs


def orphan_dev_databases(database_names: list[str], pairs: list[ProdDevPair]) -> list[str]:
    paired_dev = {p.expected_dev for p in pairs if p.dev_listed}
    orphans: list[str] = []
    for name in sorted(database_names):
        c = classify_database(name)
        if c.role == DatabaseRole.DEVELOPMENT and name not in paired_dev:
            orphans.append(name)
    return orphans

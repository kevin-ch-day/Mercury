"""Initialize local config files from examples."""

import shutil

from mercury.core.paths import (
    DATABASES_EXAMPLE,
    DATABASES_LOCAL,
    LOCAL_CONFIG,
    LOCAL_EXAMPLE,
    REPOS_EXAMPLE,
    REPOS_LOCAL,
)


def init_local_config(*, force: bool = False) -> list[str]:
    """
    Copy example config files to gitignored local paths.

    Returns list of human-readable results per file.
    """
    results: list[str] = []
    pairs = [
        (DATABASES_EXAMPLE, DATABASES_LOCAL, "databases.toml"),
        (REPOS_EXAMPLE, REPOS_LOCAL, "repos.toml"),
        (LOCAL_EXAMPLE, LOCAL_CONFIG, "local.toml"),
    ]
    for src, dest, label in pairs:
        if not src.exists():
            results.append(f"{label}: skipped (example missing: {src.name})")
            continue
        if dest.exists() and not force:
            results.append(f"{label}: already exists ({dest})")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        results.append(f"{label}: created from {src.name}")
    return results

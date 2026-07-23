"""Shared pytest fixtures and helpers for the Mercury test suite."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
CLI = [sys.executable, "-m", "mercury.cli"]
ENV_STATE_ROOT = "MERCURY_STATE_ROOT"

# Stale repos.toml path under a legacy home prefix that must not exist on the test host.
STALE_OPERATOR_REPO_PATH = Path("/home/secadmin/Laughlin/GitHub/legacy-mercury-checkout")
STALE_REPO_HOME_SUFFIX = Path("GitHub/legacy-mercury-checkout")

FIXED_DATE = "2026-05-30"
FIXED_TS = "20260530_120000"
FIXED_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)

DEFAULT_MARIADB_SOCKET = Path("/var/lib/mysql/mysql.sock")


def live_mariadb_client_config():
    """MariaDB client config for integration tests (local.toml when present)."""
    from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config

    local = repo_local_config()
    if local.exists():
        try:
            return load_mariadb_config(local)
        except Exception:
            pass
    return MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket=str(DEFAULT_MARIADB_SOCKET),
    )


def mariadb_client_connects(path: Path = DEFAULT_MARIADB_SOCKET) -> bool:
    if not mariadb_socket_available(path):
        return False
    from mercury.database.mariadb.client import client_fetch_scalar
    from mercury.database.mariadb.errors import MariaDbLiveError

    try:
        client_fetch_scalar(live_mariadb_client_config(), "SELECT 1")
    except MariaDbLiveError:
        return False
    return True


def platform_prod_databases_present() -> bool:
    if not mariadb_client_connects():
        return False
    from mercury.database.mariadb.session import fetch_user_database_names

    try:
        names = fetch_user_database_names(live_mariadb_client_config())
    except Exception:
        return False
    return "erebus_threat_intel_prod" in names


def run_cli(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``mercury.cli`` in a subprocess and return the completed process."""
    return subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=subprocess_env(env),
    )


def mariadb_socket_available(path: Path = DEFAULT_MARIADB_SOCKET) -> bool:
    try:
        return path.is_socket() or path.exists()
    except OSError:
        return False


def repo_local_config() -> Path:
    return REPO_ROOT / "config" / "local.toml"


def plain_cli_text(*chunks: str) -> str:
    """Strip ANSI and collapse whitespace for stable CLI help/assert matching."""
    import re

    combined = "".join(chunks)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", combined)
    return re.sub(r"\s+", " ", plain)


def subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for subprocess CLI tests (editable install or src on PYTHONPATH)."""
    merged = os.environ.copy()
    src = str(SRC_ROOT)
    existing = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = src if not existing else f"{src}{os.pathsep}{existing}"
    merged.setdefault(
        ENV_STATE_ROOT,
        tempfile.mkdtemp(prefix="mercury-pytest-state-"),
    )
    # Match GitHub Actions: no operator local.toml, stable help width, no color.
    merged.setdefault(
        "MERCURY_LOCAL_CONFIG",
        str(Path(tempfile.mkdtemp(prefix="mercury-pytest-nocfg-")) / "local.toml"),
    )
    merged.setdefault("MERCURY_NO_COLOR", "1")
    merged.setdefault("NO_COLOR", "1")
    merged.setdefault("COLUMNS", "120")
    merged.setdefault("MERCURY_DRY_RUN", "1")
    merged.setdefault("MERCURY_LIVE_ACTIONS", "0")
    merged.pop("MERCURY_FORCE_COLOR", None)
    if extra:
        # Allow callers to clear isolation by passing an empty string.
        for key, value in extra.items():
            if value == "" and key in merged:
                merged.pop(key, None)
            else:
                merged[key] = value
    return merged


def fedora_platform_info():
    from mercury.core.platform import PlatformInfo

    return PlatformInfo(
        system="Linux",
        release="7.0",
        distro_id="fedora",
        distro_name="Fedora Linux",
    )


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(autouse=True)
def _assume_fedora_platform_for_policy_tests(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Live-execution policy tests assume Fedora unless testing platform refusal directly."""
    node_path = str(getattr(request.node, "path", "") or "")
    if node_path.endswith("test_platform_policy.py"):
        yield
        return
    fedora = fedora_platform_info()
    monkeypatch.setattr("mercury.core.execution_policy.detect_platform", lambda: fedora)
    yield


@pytest.fixture(autouse=True)
def _reset_menu_prompt_reader() -> Iterator[None]:
    from mercury.menu.prompts import set_continue_reader, set_prompt_reader

    set_prompt_reader(None)
    set_continue_reader(lambda: None)
    yield
    set_prompt_reader(None)
    set_continue_reader(None)


@pytest.fixture(autouse=True)
def _isolate_mercury_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep pytest ledger writes off the operator USB state tree."""
    state_root = tmp_path / "mercury_state"
    state_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(ENV_STATE_ROOT, str(state_root))
    yield


# --- Storage dual-root helpers (primary HDD + transitional USB) ---

DEFAULT_PRIMARY_UUID = "715f29a9-2671-477b-8c8d-515d190addb9"
DEFAULT_LEGACY_UUID = "e4f0c7fb-132e-4867-9c16-5e4749f5c43a"


def make_storage_mount_tree(root: Path) -> dict[str, Path]:
    """Create sibling primary/legacy mount trees under ``root`` with Mercury layout dirs."""
    from mercury.core.storage_roles import MERCURY_LAYOUT_DIRS

    primary = root / "MERCURY_DATA_V2"
    legacy = root / "MERCURY_DATA_USB"
    primary.mkdir(parents=True, exist_ok=True)
    legacy.mkdir(parents=True, exist_ok=True)
    for dirname in MERCURY_LAYOUT_DIRS:
        (primary / dirname).mkdir(exist_ok=True)
        (legacy / dirname).mkdir(exist_ok=True)
    return {"primary": primary, "legacy": legacy}


def write_pre_cutover_storage_toml(
    path: Path,
    *,
    primary_mount: Path | str | None = None,
    legacy_mount: Path | str | None = None,
    include_mercury: bool = True,
) -> Path:
    """Write a minimal pre-cutover ``[storage]`` local.toml for tests."""
    primary = str(primary_mount or "/mnt/MERCURY_DATA_V2")
    legacy = str(legacy_mount or "/mnt/MERCURY_DATA_USB")
    lines = [
        "[storage]",
        'active_write_role = "legacy"',
        'migration_state = "not_started"',
        "",
        "[storage.primary]",
        'role = "canonical"',
        'label = "MERCURY_DATA_V2"',
        f'mount_path = "{primary}"',
        f'filesystem_uuid = "{DEFAULT_PRIMARY_UUID}"',
        'filesystem_type = "ext4"',
        "writable = true",
        "",
        "[storage.legacy]",
        'role = "transition_source"',
        'label = "MERCURY_DATA_USB"',
        f'mount_path = "{legacy}"',
        f'filesystem_uuid = "{DEFAULT_LEGACY_UUID}"',
        'filesystem_type = "ext4"',
        "writable = true",
        "",
        "[storage.space_policy]",
        "minimum_free_bytes = 21474836480",
        "minimum_free_percent = 10",
    ]
    if include_mercury:
        lines.extend(
            [
                "",
                "[mercury]",
                f'backup_root = "{legacy}/mercury_backups"',
                "dry_run = true",
                "live_actions_enabled = false",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_terminal_color() -> Iterator[None]:
    """Prevent color-override leaks across theme/menu tests."""
    from mercury.terminal.theme import set_color_enabled

    set_color_enabled(None)
    yield
    set_color_enabled(None)


@pytest.fixture(autouse=True)
def _isolate_operator_local_config(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Hide operator ``config/local.toml`` so unit tests match clean CI runners.

    Live MariaDB integration modules keep reading the real file via
    ``repo_local_config()`` / ``live_mariadb_client_config()``. Opt out with
    ``@pytest.mark.uses_operator_local_config``.
    """
    if "uses_operator_local_config" in request.keywords:
        yield
        return
    missing = tmp_path_factory.mktemp("isolated-local-config") / "local.toml"
    monkeypatch.setenv("MERCURY_LOCAL_CONFIG", str(missing))
    monkeypatch.setenv("MERCURY_DRY_RUN", "1")
    monkeypatch.setenv("MERCURY_LIVE_ACTIONS", "0")
    monkeypatch.setenv("MERCURY_NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "120")
    yield


@pytest.fixture(autouse=True)
def _isolate_mercury_operator_paths(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Hermetic operator paths — never touch live host share or Mercury HDD."""
    root = tmp_path_factory.mktemp("mercury_hermetic")
    host = root / "host_maintenance.json"
    ledger = root / "transition_ledger.jsonl"
    operator = root / "operator_root"
    backups = root / "backups"
    logs = root / "logs"
    locks = root / "locks"
    for path in (operator, backups, logs, locks):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MERCURY_TEST_ISOLATION", "1")
    monkeypatch.setenv("MERCURY_EVENT_ENVIRONMENT", "test")
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(host))
    monkeypatch.setenv("MERCURY_TRANSITION_LEDGER_PATH", str(ledger))
    monkeypatch.setenv("MERCURY_BACKUP_ROOT", str(backups))
    monkeypatch.setenv("MERCURY_OPERATOR_ROOT", str(operator))
    monkeypatch.setenv("MERCURY_LOG_ROOT", str(logs))
    monkeypatch.setenv("MERCURY_LOG_DIR", str(logs))
    monkeypatch.setenv("MERCURY_OPERATION_LOCK_DIR", str(locks))
    monkeypatch.delenv("MERCURY_ACTIVE_OPERATION", raising=False)
    yield


@pytest.fixture(autouse=True)
def _forbid_live_mercury_path_access(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail loudly if a test opens/writes known live Mercury paths."""
    from mercury.storage.host_maintenance import assert_not_live_mercury_path

    live_files = {
        (Path.home() / ".local" / "share" / "mercury" / "host_maintenance.json").resolve(),
        (Path.home() / ".local" / "share" / "mercury" / "transition_ledger.jsonl").resolve(),
    }
    live_roots = (
        Path("/mnt/MERCURY_DATA_V2").resolve(),
        Path("/mnt/MERCURY_DATA_USB").resolve(),
    )
    real_open = open

    def guarded_open(file, mode="r", *args, **kwargs):
        path = Path(file).expanduser() if not hasattr(file, "read") else None
        if path is not None:
            write_mode = any(token in str(mode) for token in ("w", "a", "x", "+"))
            if write_mode:
                assert_not_live_mercury_path(path, purpose=f"open({mode})")
                try:
                    resolved = path.resolve()
                except OSError:
                    resolved = path
                if resolved in live_files:
                    raise RuntimeError(f"TEST ISOLATION: open refused for {resolved}")
                for live_root in live_roots:
                    try:
                        resolved.relative_to(live_root)
                        raise RuntimeError(
                            f"TEST ISOLATION: open refused under live path {live_root}"
                        )
                    except ValueError:
                        pass
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)
    yield


@pytest.fixture
def storage_mounts(tmp_path: Path) -> dict[str, Path]:
    """Primary + legacy mount trees under tmp_path."""
    return make_storage_mount_tree(tmp_path)


@pytest.fixture
def pre_cutover_storage_config(tmp_path: Path, storage_mounts: dict[str, Path]) -> Path:
    """local.toml pointing at tmp primary/legacy mounts (active writer = legacy)."""
    return write_pre_cutover_storage_toml(
        tmp_path / "local.toml",
        primary_mount=storage_mounts["primary"],
        legacy_mount=storage_mounts["legacy"],
    )

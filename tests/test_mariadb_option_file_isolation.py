"""Dedicated credentials must not be overridden by ~/.my.cnf or other option files.

Uses the real client's ``--print-defaults`` (option-file parsing only, no
connection) against a temporary HOME containing a hostile ``~/.my.cnf``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mercury.backup.dump_planner import build_dump_argv_for_config
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.client import (
    build_client_argv,
    client_process_credentials,
    prepend_client_defaults,
)
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.restore.restore_runner import build_import_argv

HOME_PASSWORD = "HOME_MYCNF_PASSWORD"
HOME_USER = "home_mycnf_user"
DEDICATED_PASSWORD = "DEDICATED_RESTORE_PASSWORD"


def _restore_config(**overrides) -> MariaDbConnectionConfig:
    values = {
        "host": "localhost",
        "user": "mercury_dev_restore",
        "password": DEDICATED_PASSWORD,
        "unix_socket": "/var/lib/mysql/mysql.sock",
        "use_client": True,
    }
    values.update(overrides)
    return MariaDbConnectionConfig(**values)


@pytest.fixture
def hostile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    mycnf = home / ".my.cnf"
    mycnf.write_text(
        "[client]\n"
        f"user={HOME_USER}\n"
        f"password={HOME_PASSWORD}\n"
        "host=home-mycnf-host\n"
        "[mysqldump]\n"
        "skip-routines\n",
        encoding="utf-8",
    )
    mycnf.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    for name in ("MYSQL_HOME", "MARIADB_HOME", "MYSQL_GROUP_SUFFIX"):
        monkeypatch.delenv(name, raising=False)
    return home


def _print_defaults(argv: list[str], env: dict[str, str]) -> str:
    """Run ``tool <defaults option> --print-defaults``: parse option files, never connect."""
    if shutil.which(argv[0]) is None:
        pytest.skip(f"{argv[0]} not on PATH")
    probe = [argv[0], argv[1], "--print-defaults"]
    result = subprocess.run(
        probe, capture_output=True, text=True, env=env, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _client_tool_available(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not on PATH")


def test_control_defaults_extra_file_is_overridden_by_home_mycnf(hostile_home: Path) -> None:
    """Prove the fixture is effective: the old flag lets ~/.my.cnf win."""
    _client_tool_available("mariadb")
    cnf = hostile_home.parent / "extra.cnf"
    cnf.write_text(f'[client]\npassword="{DEDICATED_PASSWORD}"\n', encoding="utf-8")
    cnf.chmod(0o600)
    out = _print_defaults(["mariadb", f"--defaults-extra-file={cnf}"], dict(os.environ))
    assert out.rindex(f"--password={HOME_PASSWORD}") > out.index(
        f"--password={DEDICATED_PASSWORD}"
    )


@pytest.mark.parametrize(
    "build_argv",
    [
        pytest.param(lambda cfg: build_client_argv(cfg, "SELECT 1"), id="client-query"),
        pytest.param(lambda cfg: build_import_argv(cfg, "_restorecheck_x"), id="restore-import"),
        pytest.param(
            lambda cfg: build_dump_argv_for_config(
                "erebus_threat_intel_prod", BACKUP_KIND_FULL, cfg
            ),
            id="dump",
        ),
    ],
)
@pytest.mark.parametrize("password", [DEDICATED_PASSWORD, ""])
def test_dedicated_credentials_ignore_home_mycnf(hostile_home: Path, build_argv, password) -> None:
    cfg = _restore_config(password=password)
    argv = build_argv(cfg)
    _client_tool_available(argv[0])
    with client_process_credentials(cfg) as (env, extra):
        live = prepend_client_defaults(argv, extra)
        assert live[1].startswith("--defaults-file=")
        assert not any(a.startswith("--defaults-extra-file") for a in live)
        out = _print_defaults(live, env)
    assert f"--password={password}" in out
    assert HOME_PASSWORD not in out
    assert HOME_USER not in out
    assert "home-mycnf-host" not in out
    assert "skip-routines" not in out
    # Identity and transport stay on the command line, after the defaults file.
    assert live[live.index("-u") + 1] == "mercury_dev_restore"
    assert "--protocol=SOCKET" in live
    assert "--socket=/var/lib/mysql/mysql.sock" in live


@pytest.mark.parametrize("password", [DEDICATED_PASSWORD, ""])
def test_exclusive_defaults_file_is_private_and_has_no_includes(hostile_home, password):
    import stat
    cfg = _restore_config(password=password)
    with client_process_credentials(cfg) as (_, extra):
        path = Path(extra[0].split("=", 1)[1])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        content = path.read_text()
        assert content == f'[client]\npassword="{password}"\n'
        assert "!include" not in content  # No /etc/my.cnf.d or other ambient includes.
    assert not path.exists()


def test_host_repair_has_only_exclusive_mariadb_invocations():
    script = (Path(__file__).resolve().parents[1] / "scripts/repair-mercury-host.sh").read_text()
    calls = [line.strip() for line in script.splitlines() if line.strip().startswith("mariadb ")]
    assert calls == ['mariadb --defaults-file="$MERCURY_REPAIR_DEFAULTS" <<EOF']
    assert "umask 077" in script
    assert 'trap ' in script
    assert 'password=""' in script

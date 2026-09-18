"""MariaDB connection settings from config/local.toml and environment."""

import os
import re
import stat
from pathlib import Path

import tomllib
from pydantic import BaseModel, Field

from mercury.core.paths import LOCAL_EXAMPLE, resolve_local_config

DEFAULT_PASSWORD_ENV = "MERCURY_MARIADB_PASSWORD"
DEFAULT_UNIX_SOCKET = "/var/lib/mysql/mysql.sock"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "localhost.localdomain"})
_PASSWORD_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MariaDbConfigError(Exception):
    """Missing or invalid MariaDB configuration."""


class MariaDbConnectionConfig(BaseModel):
    host: str
    port: int = 3306
    user: str
    password: str = Field(default="", repr=False)
    password_env: str | None = None
    password_file: str | None = Field(default=None, repr=False)
    connect_timeout: int = 10
    ssl_disabled: bool = True
    unix_socket: str | None = None
    use_client: bool = False

    @property
    def uses_socket(self) -> bool:
        return bool(self.unix_socket)

    @property
    def uses_loopback_tcp(self) -> bool:
        return not self.unix_socket and is_loopback_host(self.host)


def is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def assert_tcp_tls_policy(
    *,
    host: str,
    unix_socket: str | None,
    ssl_disabled: bool,
) -> None:
    """Refuse cleartext MariaDB TCP to a non-loopback host."""
    if unix_socket:
        return
    if ssl_disabled and not is_loopback_host(host):
        raise MariaDbConfigError(
            f"Remote MariaDB TCP ({host}) requires TLS. "
            "Set ssl_disabled = false in config/local.toml."
        )


def assert_connection_tls(config: MariaDbConnectionConfig) -> None:
    assert_tcp_tls_policy(
        host=config.host,
        unix_socket=config.unix_socket,
        ssl_disabled=config.ssl_disabled,
    )


def _configured_secret_sources(section: dict[str, object]) -> list[str]:
    return [
        name for name in ("password_env", "password_file", "password")
        if section.get(name) is not None and str(section[name]).strip()
    ]


def _read_password_file(raw_path: object) -> tuple[str, str]:
    path = Path(str(raw_path)).expanduser()
    if path.is_symlink():
        raise MariaDbConfigError(f"MariaDB password file must not be a symlink: {path}")
    try:
        info = path.stat()
    except OSError as exc:
        raise MariaDbConfigError(f"MariaDB password file is unavailable: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise MariaDbConfigError(f"MariaDB password file is not a regular file: {path}")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise MariaDbConfigError(f"MariaDB password file is not owned by the current user: {path}")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise MariaDbConfigError(
            f"MariaDB password file has insecure permissions: {path}. "
            "Expected owner-only access (0600)."
        )
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MariaDbConfigError(f"MariaDB password file is unreadable: {path}") from exc
    password = value.rstrip("\r\n")
    if not password:
        raise MariaDbConfigError(f"MariaDB password file is empty: {path}")
    return password, str(path)


def _resolve_password(section: dict[str, object], *, optional: bool = False) -> tuple[str, str | None]:
    sources = _configured_secret_sources(section)
    if len(sources) > 1:
        raise MariaDbConfigError(
            "MariaDB configuration has ambiguous secret sources; configure only one of "
            "password_env, password_file, or password."
        )
    if not sources:
        if optional:
            return "", None
        raise MariaDbConfigError(
            "MariaDB password not configured. Set password_env in config/local.toml "
            f"(recommended, e.g. '{DEFAULT_PASSWORD_ENV}'), password_file, "
            "or enable use_client + unix_socket for local Fedora socket auth. "
            f"See {LOCAL_EXAMPLE.name}."
        )
    source = sources[0]
    if source == "password_env":
        env_name = str(section["password_env"]).strip()
        if not _PASSWORD_ENV_NAME.fullmatch(env_name):
            raise MariaDbConfigError(
                f"MariaDB password_env name is not a safe environment variable: {env_name!r}"
            )
        value = os.environ.get(env_name)
        if not value:
            if optional:
                return "", None
            raise MariaDbConfigError(
                f"Password environment variable '{env_name}' is not set or empty. "
                "Export it before running live database commands."
            )
        return value, None
    if source == "password_file":
        return _read_password_file(section["password_file"])
    return str(section["password"]), None


def _load_mariadb_section(section_name: str, path: Path | None = None) -> MariaDbConnectionConfig:
    """Load one explicitly named MariaDB credential lane from local config."""
    config_path = path or resolve_local_config()
    if not config_path.exists():
        raise MariaDbConfigError(
            f"{config_path} not found. Run: mercury config init\n"
            f"Then configure [{section_name}] in {resolve_local_config().name} "
            f"(see {LOCAL_EXAMPLE.name})."
        )

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    mariadb = data.get(section_name)
    if not isinstance(mariadb, dict):
        raise MariaDbConfigError(f"[{section_name}] section missing in {config_path}")

    user = mariadb.get("user")
    if not user or not str(user).strip():
        raise MariaDbConfigError(f"[{section_name}].user is required in {config_path}")

    use_client = bool(mariadb.get("use_client", False))
    unix_socket_raw = mariadb.get("unix_socket")
    unix_socket = str(unix_socket_raw).strip() if unix_socket_raw else None
    password_optional = use_client and bool(unix_socket)

    host_raw = mariadb.get("host")
    host = str(host_raw).strip() if host_raw else "localhost"
    if not host:
        host = "localhost"

    port_raw = mariadb.get("port", 3306)
    try:
        port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise MariaDbConfigError(f"[{section_name}].port must be an integer in {config_path}") from exc

    timeout_raw = mariadb.get("connect_timeout", 10)
    try:
        connect_timeout = int(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise MariaDbConfigError(
            f"[{section_name}].connect_timeout must be an integer in {config_path}"
        ) from exc

    password_env = mariadb.get("password_env")
    password, password_file = _resolve_password(mariadb, optional=password_optional)

    return MariaDbConnectionConfig(
        host=host,
        port=port,
        user=str(user).strip(),
        password=password,
        password_env=str(password_env).strip() if password_env else None,
        password_file=password_file,
        connect_timeout=connect_timeout,
        ssl_disabled=bool(mariadb.get("ssl_disabled", True)),
        unix_socket=unix_socket,
        use_client=use_client,
    )


def load_mariadb_config(path: Path | None = None) -> MariaDbConnectionConfig:
    """Load [mariadb], the general/source operator credential lane."""
    return _load_mariadb_section("mariadb", path)


def load_mariadb_restore_config(path: Path | None = None) -> MariaDbConnectionConfig:
    """Load [mariadb_restore], required for ordinary prod→dev resets."""
    return _load_mariadb_section("mariadb_restore", path)

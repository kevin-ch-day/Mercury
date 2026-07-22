"""MariaDB connection settings from config/local.toml and environment."""

import os
from pathlib import Path

import tomllib
from pydantic import BaseModel, Field

from mercury.core.paths import LOCAL_EXAMPLE, resolve_local_config

DEFAULT_PASSWORD_ENV = "MERCURY_MARIADB_PASSWORD"
DEFAULT_UNIX_SOCKET = "/var/lib/mysql/mysql.sock"


class MariaDbConfigError(Exception):
    """Missing or invalid MariaDB configuration."""


class MariaDbConnectionConfig(BaseModel):
    host: str
    port: int = 3306
    user: str
    password: str = Field(default="", repr=False)
    password_env: str | None = None
    connect_timeout: int = 10
    ssl_disabled: bool = True
    unix_socket: str | None = None
    use_client: bool = False

    @property
    def uses_socket(self) -> bool:
        return bool(self.unix_socket)


def _resolve_password(mariadb: dict[str, object], *, optional: bool = False) -> str:
    password_env = mariadb.get("password_env")
    if password_env is not None and str(password_env).strip():
        env_name = str(password_env).strip()
        value = os.environ.get(env_name)
        if not value:
            if optional:
                return ""
            raise MariaDbConfigError(
                f"Password environment variable '{env_name}' is not set or empty. "
                f"Export it before running live database commands."
            )
        return value

    if "password" in mariadb:
        pwd = mariadb.get("password")
        if pwd is not None and str(pwd).strip():
            return str(pwd)

    if optional:
        return ""

    raise MariaDbConfigError(
        "MariaDB password not configured. Set password_env in config/local.toml "
        f"(recommended, e.g. '{DEFAULT_PASSWORD_ENV}') and export the variable, "
        "or enable use_client + unix_socket for local Fedora socket auth. "
        f"See {LOCAL_EXAMPLE.name}."
    )


def load_mariadb_config(path: Path | None = None) -> MariaDbConnectionConfig:
    """Load [mariadb] from config/local.toml."""
    config_path = path or resolve_local_config()
    if not config_path.exists():
        raise MariaDbConfigError(
            f"{config_path} not found. Run: mercury config init\n"
            f"Then configure [mariadb] in {resolve_local_config().name} "
            f"(see {LOCAL_EXAMPLE.name})."
        )

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    mariadb = data.get("mariadb")
    if not isinstance(mariadb, dict):
        raise MariaDbConfigError(
            f"[mariadb] section missing in {config_path}. "
            f"Add host, user, and connection settings."
        )

    user = mariadb.get("user")
    if not user or not str(user).strip():
        raise MariaDbConfigError(f"[mariadb].user is required in {config_path}")

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
        raise MariaDbConfigError(f"[mariadb].port must be an integer in {config_path}") from exc

    timeout_raw = mariadb.get("connect_timeout", 10)
    try:
        connect_timeout = int(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise MariaDbConfigError(
            f"[mariadb].connect_timeout must be an integer in {config_path}"
        ) from exc

    password_env = mariadb.get("password_env")
    password = _resolve_password(mariadb, optional=password_optional)

    return MariaDbConnectionConfig(
        host=host,
        port=port,
        user=str(user).strip(),
        password=password,
        password_env=str(password_env).strip() if password_env else None,
        connect_timeout=connect_timeout,
        ssl_disabled=bool(mariadb.get("ssl_disabled", True)),
        unix_socket=unix_socket,
        use_client=use_client,
    )

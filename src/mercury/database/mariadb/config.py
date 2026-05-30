"""MariaDB connection settings from config/local.toml and environment."""

import os
from pathlib import Path

import tomllib
from pydantic import BaseModel, Field

from mercury.paths import LOCAL_CONFIG, LOCAL_EXAMPLE

DEFAULT_PASSWORD_ENV = "MERCURY_MARIADB_PASSWORD"


class MariaDbConfigError(Exception):
    """Missing or invalid MariaDB configuration."""


class MariaDbConnectionConfig(BaseModel):
    host: str
    port: int = 3306
    user: str
    password: str = Field(repr=False)
    password_env: str | None = None
    connect_timeout: int = 10
    ssl_disabled: bool = True


def _resolve_password(mariadb: dict[str, object]) -> str:
    password_env = mariadb.get("password_env")
    if password_env is not None and str(password_env).strip():
        env_name = str(password_env).strip()
        value = os.environ.get(env_name)
        if not value:
            raise MariaDbConfigError(
                f"Password environment variable '{env_name}' is not set or empty. "
                f"Export it before running: mercury db discover"
            )
        return value

    if "password" in mariadb:
        pwd = mariadb.get("password")
        if pwd is not None and str(pwd).strip():
            return str(pwd)

    raise MariaDbConfigError(
        "MariaDB password not configured. Set password_env in config/local.toml "
        f"(recommended, e.g. '{DEFAULT_PASSWORD_ENV}') and export the variable, "
        f"or copy {LOCAL_EXAMPLE.name} to {LOCAL_CONFIG.name}."
    )


def load_mariadb_config(path: Path | None = None) -> MariaDbConnectionConfig:
    """Load [mariadb] from config/local.toml."""
    config_path = path or LOCAL_CONFIG
    if not config_path.exists():
        raise MariaDbConfigError(
            f"{config_path} not found. Run: mercury config init\n"
            f"Then configure [mariadb] in {LOCAL_CONFIG.name} "
            f"(see {LOCAL_EXAMPLE.name})."
        )

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    mariadb = data.get("mariadb")
    if not isinstance(mariadb, dict):
        raise MariaDbConfigError(
            f"[mariadb] section missing in {config_path}. "
            f"Add host, port, user, and password_env."
        )

    host = mariadb.get("host")
    user = mariadb.get("user")
    if not host or not str(host).strip():
        raise MariaDbConfigError(f"[mariadb].host is required in {config_path}")
    if not user or not str(user).strip():
        raise MariaDbConfigError(f"[mariadb].user is required in {config_path}")

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
    password = _resolve_password(mariadb)

    return MariaDbConnectionConfig(
        host=str(host).strip(),
        port=port,
        user=str(user).strip(),
        password=password,
        password_env=str(password_env).strip() if password_env else None,
        connect_timeout=connect_timeout,
        ssl_disabled=bool(mariadb.get("ssl_disabled", True)),
    )

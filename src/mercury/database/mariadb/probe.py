"""MariaDB client tooling probe and legacy planning helpers."""

import platform
import shutil
from typing import Literal

from pydantic import BaseModel, Field

from mercury.safety import LIVE_ACTIONS_ENABLED, MODE_SEED

MARIADB_CLIENT_TOOLS = (
    "mariadb",
    "mysql",
    "mariadb-dump",
    "mysqldump",
)


class ToolingProbe(BaseModel):
    platform: str
    tools: dict[str, str] = Field(default_factory=dict)


class ReadOnlyDiscoveryPlan(BaseModel):
    """Outline of read-only discovery (superseded by database.mariadb.live for M5+)."""

    status: Literal["seed_disabled", "ready_not_executed", "live_disabled", "implemented"]
    mode: str = MODE_SEED
    live_actions_enabled: bool = LIVE_ACTIONS_ENABLED
    planned_steps: list[str] = Field(default_factory=list)
    planned_sql: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def probe_client_tooling() -> ToolingProbe:
    tools: dict[str, str] = {}
    for name in MARIADB_CLIENT_TOOLS:
        path = shutil.which(name)
        tools[name] = path if path else "not found"
    return ToolingProbe(platform=platform.system(), tools=tools)


def build_readonly_discovery_plan(
    host: str = "localhost",
    port: int = 3306,
) -> ReadOnlyDiscoveryPlan:
    """Document read-only discovery; live path uses mercury db discover (no --demo)."""
    plan = ReadOnlyDiscoveryPlan(status="implemented")
    plan.planned_steps = [
        f"Connect read-only to MariaDB at {host}:{port} (config/local.toml [mariadb]).",
        "Execute: SHOW DATABASES;",
        "Classify each database name via mercury.database.core.classifier.",
    ]
    plan.planned_sql = ["SHOW DATABASES;"]
    plan.notes = [
        "Live discovery: mercury db discover (requires pymysql and MERCURY_MARIADB_PASSWORD).",
        "Demo discovery: mercury db discover --demo (no server).",
        "No CREATE/DROP/ALTER; backups not executed from this module.",
    ]
    tooling = probe_client_tooling()
    if any(v != "not found" for v in tooling.tools.values()):
        plan.notes.append("MariaDB/MySQL client binaries found on PATH.")
    return plan

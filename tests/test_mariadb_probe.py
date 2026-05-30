"""Tests for MariaDB tooling probe and read-only discovery plan."""

from mercury.database import build_readonly_discovery_plan, probe_client_tooling
from mercury.safety import LIVE_ACTIONS_ENABLED, MODE_SEED


def test_probe_client_tooling_returns_all_tools() -> None:
    tooling = probe_client_tooling()
    assert tooling.platform
    assert "mariadb" in tooling.tools
    assert "mariadb-dump" in tooling.tools


def test_readonly_plan_is_not_executed_in_seed() -> None:
    plan = build_readonly_discovery_plan()
    assert plan.mode == MODE_SEED
    assert plan.live_actions_enabled is LIVE_ACTIONS_ENABLED
    assert "SHOW DATABASES" in plan.planned_sql[0]
    assert plan.status in (
        "seed_disabled",
        "live_disabled",
        "ready_not_executed",
        "implemented",
    )

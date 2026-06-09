"""Terminal output for deployment preflight."""

from __future__ import annotations

from mercury.deploy.models import DeploymentPreflight
from mercury.terminal import screen as display_screen


def print_deployment_preflight(preflight: DeploymentPreflight, *, compact: bool = False) -> None:
    display_screen.write_section("DEPLOYMENT PREFLIGHT")
    display_screen.write_fields(
        {
            "Host": preflight.hostname,
            "Ready": "yes" if preflight.ready else "no",
        }
    )
    for check in preflight.checks:
        tag = {"ready": "ok", "warning": "warn", "blocked": "fail"}.get(check.level, "info")
        line = check.label if not check.detail else f"{check.label}: {check.detail}"
        display_screen.write_status(tag, line)
    if preflight.existing_databases and not compact:
        display_screen.write_summary(
            "Databases on server: " + ", ".join(preflight.existing_databases)
        )

"""Terminal output for deployment use-case guidance."""

from __future__ import annotations

from mercury.deploy.use_cases import DeployUseCase
from mercury.terminal import screen as display_screen


def print_deploy_use_cases(cases: list[DeployUseCase]) -> None:
    display_screen.write_section("DEPLOYMENT USE CASES")
    for index, case in enumerate(cases, start=1):
        display_screen.write_summary(f"{index}. {case.title}")
        display_screen.write_summary(f"   {case.summary}")
        for command in case.commands:
            display_screen.write_summary(f"   → {command}")
        display_screen.write_blank()

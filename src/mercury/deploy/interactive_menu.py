"""Deploy to this system — databases, repositories, and combined planning."""

from __future__ import annotations

from mercury.core.execution_policy import load_execution_policy
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.preflight import run_deployment_preflight
from mercury.deploy.repos.build_plan import build_repo_deploy_plan
from mercury.deploy.repos.preflight import run_repo_deploy_preflight
from mercury.deploy.repos.runner import execute_repo_deploy_batch
from mercury.deploy.repos.selection import resolve_repo_deploy_candidates
from mercury.deploy.repos.terminal.plan import print_repo_deploy_plan
from mercury.deploy.repos.terminal.summary import print_repo_deploy_summary
from mercury.deploy.runner import execute_deployment_batch
from mercury.deploy.system import build_system_deploy_plan, print_system_deploy_plan
from mercury.deploy.menu_status import (
    database_deploy_status_rows,
    deploy_hub_status_rows,
    repository_deploy_status_rows,
)
from mercury.deploy.terminal.plan import print_deployment_plan
from mercury.deploy.terminal.preflight import print_deployment_preflight
from mercury.deploy.terminal.summary import print_deployment_summary
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.repo import inspect_repositories, load_repo_definitions
from mercury.repo.terminal import print_repo_statuses
from mercury.terminal import screen as display_screen

HUB_TITLE = "Deploy to This System"
DB_TITLE = "Deploy Databases"
REPO_TITLE = "Deploy Repositories"


def _write_status_rows(rows: list[str]) -> None:
    from mercury import output
    from mercury.terminal.theme import rule_line

    if not rows:
        return
    output.write(rule_line())
    for row in rows:
        output.write(row)
    output.write(rule_line())


def read_deploy_choice() -> str | None:
    return read_submenu_choice()


def _render_hub(*, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(HUB_TITLE)
    _write_status_rows(deploy_hub_status_rows())
    render_submenu(
        [
            ("1", "Deploy databases"),
            ("2", "Deploy repositories"),
            ("3", "Full system dry-run plan"),
            ("4", "Preflight all"),
            ("5", "Show deployment use cases"),
        ]
    )


def _render_db_menu(*, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(DB_TITLE)
    _write_status_rows(database_deploy_status_rows())
    policy = load_execution_policy()
    options = [
        ("1", "Show latest verified backup set"),
        ("2", "Dry-run deploy latest verified set"),
        (
            "3",
            "Deploy latest verified set (LIVE — y/n confirm)"
            if policy.live_execution_allowed()
            else "Deploy latest verified set (blocked — enable live mode)",
        ),
        ("4", "Preflight checks"),
    ]
    render_submenu(options)
    if policy.live_execution_allowed():
        display_screen.write_hint("Option 3 imports verified USB backups into MariaDB.")


def _render_repo_menu(*, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(REPO_TITLE)
    _write_status_rows(repository_deploy_status_rows())
    render_submenu(
        [
            ("1", "Show configured repository status"),
            ("2", "Dry-run repository deploy plan"),
            ("3", "Deploy missing repos from GitHub"),
            ("4", "Deploy missing repos from USB bundles"),
            ("5", "Preflight checks"),
        ]
    )


def _db_submenu() -> None:
    show_title = True
    while True:
        _render_db_menu(show_title=show_title)
        choice = read_deploy_choice()
        if choice in {None, "0"}:
            return
        show_title = False
        if choice == "1":
            from mercury.deploy.target_status import target_status_label

            plan = build_deployment_plan(execute=False)
            for candidate in plan.candidates:
                action = candidate.deploy_action.replace("_", " ")
                status = target_status_label(candidate.target_status)
                display_screen.write_summary(
                    f"{candidate.source_database}\n"
                    f"  backup: {candidate.dump_path}\n"
                    f"  target: {status} → {action}"
                )
        elif choice == "2":
            print_deployment_plan(build_deployment_plan(execute=False))
        elif choice == "3":
            _live_db_deploy()
        elif choice == "4":
            print_deployment_preflight(run_deployment_preflight())
        else:
            menu_display.write_status("fail", menu_prompts.invalid_choice_message(choice))
        pause_and_redraw()


def _live_db_deploy() -> None:
    policy = load_execution_policy()
    if not policy.live_execution_allowed():
        menu_display.write_status("warn", policy.refusal_reason() or "Live deployment is not enabled.")
        print_deployment_plan(build_deployment_plan(execute=False))
        return
    plan = build_deployment_plan(execute=True)
    print_deployment_plan(plan)
    if plan.summary_message and not plan.deployment_needed:
        menu_display.write_status("ok", plan.summary_message)
        return
    if not plan.allowed:
        if plan.block_count and not plan.import_count:
            menu_display.write_status(
                "warn",
                "Live deployment blocked — existing target databases are protected by default policy.",
            )
        return
    targets = [
        c.target_database
        for c in plan.candidates
        if c.deploy_action in {"CREATE_AND_IMPORT", "OVERWRITE_DROP"}
    ]
    if not targets:
        menu_display.write_status("warn", plan.summary_message or "No databases eligible for live deployment.")
        return
    skip_names = [c.target_database for c in plan.candidates if c.deploy_action == "SKIP"]
    confirm = f"Deploy {len(targets)} database(s)? ({', '.join(targets)})"
    if skip_names:
        confirm += f" — skip {len(skip_names)} existing ({', '.join(skip_names)})"
    if menu_prompts.ask_yes_no(confirm, default=False) is not True:
        menu_display.write_summary("Deployment cancelled.")
        return
    print_deployment_summary(execute_deployment_batch(policy=policy, execute=True))


def _repo_submenu() -> None:
    show_title = True
    while True:
        _render_repo_menu(show_title=show_title)
        choice = read_deploy_choice()
        if choice in {None, "0"}:
            return
        show_title = False
        if choice == "1":
            print_repo_statuses(inspect_repositories(load_repo_definitions()), verbose=True)
        elif choice == "2":
            print_repo_deploy_plan(build_repo_deploy_plan(execute=False, source_mode="auto"))
        elif choice == "3":
            _live_repo_deploy(source_mode="github")
        elif choice == "4":
            _live_repo_deploy(source_mode="usb")
        elif choice == "5":
            print_deployment_preflight(run_repo_deploy_preflight())
        else:
            menu_display.write_status("fail", menu_prompts.invalid_choice_message(choice))
        pause_and_redraw()


def _live_repo_deploy(*, source_mode: str) -> None:
    policy = load_execution_policy()
    if not policy.live_execution_allowed():
        menu_display.write_status("warn", policy.refusal_reason() or "Live deployment is not enabled.")
        print_repo_deploy_plan(build_repo_deploy_plan(execute=False, source_mode=source_mode))
        return
    plan = build_repo_deploy_plan(execute=True, source_mode=source_mode)
    print_repo_deploy_plan(plan)
    if not plan.allowed:
        return
    targets = [c.display_name for c in plan.candidates if c.source != "none" and not c.exists_on_system]
    if not targets:
        menu_display.write_status("warn", "No repositories eligible for live deployment.")
        return
    if menu_prompts.ask_yes_no(
        f"Deploy {len(targets)} repository/repositories? ({', '.join(targets)})",
        default=False,
    ) is not True:
        menu_display.write_summary("Deployment cancelled.")
        return
    print_repo_deploy_summary(
        execute_repo_deploy_batch(policy=policy, execute=True, source_mode=source_mode)
    )


def run_deploy_menu(*, interactive: bool = True) -> None:
    show_title = True
    while True:
        _render_hub(show_title=show_title)
        if not interactive:
            return
        choice = read_deploy_choice()
        if choice in {None, "0"}:
            return
        show_title = False
        if choice == "1":
            _db_submenu()
        elif choice == "2":
            _repo_submenu()
        elif choice == "3":
            print_system_deploy_plan(build_system_deploy_plan(execute=False))
        elif choice == "4":
            print_deployment_preflight(run_deployment_preflight())
            print_deployment_preflight(run_repo_deploy_preflight())
        elif choice == "5":
            from mercury.deploy.terminal.use_cases import print_deploy_use_cases
            from mercury.deploy.use_cases import detect_deploy_use_cases

            print_deploy_use_cases(detect_deploy_use_cases())
        else:
            menu_display.write_status("fail", menu_prompts.invalid_choice_message(choice))
        pause_and_redraw()

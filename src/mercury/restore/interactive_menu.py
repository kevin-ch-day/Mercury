"""Compatibility entries for restore-check operator menus."""

from __future__ import annotations


def run_restorecheck_cleanup() -> None:
    """Drop leftover ``_restorecheck_*`` databases through the policy gate."""
    from mercury.core.execution_policy import load_execution_policy
    from mercury.restore.check_cleanup import (
        cleanup_restorecheck_databases,
        discover_restorecheck_names,
    )
    from mercury.restore.terminal.check_cleanup import (
        print_restorecheck_cleanup_batch,
    )

    policy = load_execution_policy()
    batch = cleanup_restorecheck_databases(
        discover_restorecheck_names(),
        execute=policy.live_execution_allowed(),
    )
    print_restorecheck_cleanup_batch(batch, compact=True)


def run_restore_menu(*, interactive: bool = True) -> None:
    """Open the consolidated recovery dashboard."""
    from mercury.restore.interactive_dashboard import run_recovery_dashboard

    run_recovery_dashboard(interactive=interactive)

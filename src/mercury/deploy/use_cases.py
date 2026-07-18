"""Detect operator recovery-deployment scenarios for a prepared Fedora host."""

from __future__ import annotations

from dataclasses import dataclass

from mercury.core.paths import REPOS_LOCAL
from mercury.deploy.repos.build_plan import build_repo_deploy_plan
from mercury.deploy.snapshot import build_deployment_snapshot
from mercury.deploy.repos.selection import resolve_repo_deploy_candidates
from mercury.repo import load_repo_definitions
from mercury.repo.path_repair import stale_repo_path_detail, web_repo_parent_dirs_needing_prep


@dataclass(frozen=True)
class DeployUseCase:
    case_id: str
    title: str
    summary: str
    commands: tuple[str, ...]


def detect_deploy_use_cases() -> list[DeployUseCase]:
    """Return applicable deployment scenarios for the current host."""
    cases: list[DeployUseCase] = []
    db_snapshot = build_deployment_snapshot(execute=False)
    repo_plan = build_repo_deploy_plan(execute=False, source_mode="auto")
    candidates = resolve_repo_deploy_candidates(source_mode="auto")
    repos = load_repo_definitions()

    dbs_need_import = db_snapshot.import_count > 0
    dbs_already_on_server = db_snapshot.on_server_count > 0 and db_snapshot.import_count == 0
    missing_repos = [c for c in candidates if not c.exists_on_system and c.source != "none"]
    stale_paths = [
        detail
        for repo in repos
        if (detail := stale_repo_path_detail(repo.path)) is not None
    ]

    if stale_paths:
        cases.append(
            DeployUseCase(
                case_id="stale_repos_config",
                title="Stale repository paths in config/repos.toml",
                summary="Repository paths still reference a prior workstation home directory.",
                commands=(
                    "./run.sh repo init-config --force",
                    "./run.sh deploy repos --dry-run",
                ),
            )
        )

    web_parents = web_repo_parent_dirs_needing_prep(repos)
    if web_parents:
        parent = web_parents[0]
        cases.append(
            DeployUseCase(
                case_id="web_repo_directories",
                title="Prepare /var/www/html before web repository deploy",
                summary="Web repositories clone under /var/www/html; the parent directory may require sudo setup.",
                commands=(
                    f"sudo mkdir -p {parent}",
                    f"sudo chown $USER:$USER {parent}",
                    "./run.sh deploy repos --dry-run",
                ),
            )
        )

    if not REPOS_LOCAL.exists():
        cases.append(
            DeployUseCase(
                case_id="repos_config_missing",
                title="Repository config not initialized",
                summary="Mercury cannot plan GitHub or operator-storage repository deployment without config/repos.toml.",
                commands=("./run.sh repo init-config",),
            )
        )

    if (
        not db_snapshot.deployment_needed
        and db_snapshot.on_server_count
        and db_snapshot.missing_source_count == 0
    ):
        cases.append(
            DeployUseCase(
                case_id="databases_already_deployed",
                title="Protected databases already on MariaDB",
                summary=(
                    f"{db_snapshot.on_server_count} protected database(s) already exist; "
                    "default deploy policy will skip them."
                ),
                commands=(
                    "./run.sh db inventory",
                    "./run.sh deploy db --dry-run",
                ),
            )
        )

    if dbs_need_import and missing_repos:
        cases.append(
            DeployUseCase(
                case_id="fresh_full_rebuild",
                title="Fresh workstation — databases and repositories",
                summary="Verified operator-storage database backups and repository sources are available; prod DBs and repos are missing.",
                commands=(
                    "./run.sh deploy system --dry-run",
                    "./run.sh menu",
                ),
            )
        )
    elif dbs_need_import:
        cases.append(
            DeployUseCase(
                case_id="deploy_databases_only",
                title="Deploy databases only",
                summary=f"{db_snapshot.import_count} protected database(s) missing on MariaDB; verified operator-storage SQL backups are ready.",
                commands=("./run.sh deploy db --dry-run",),
            )
        )
    elif missing_repos and dbs_already_on_server:
        cases.append(
            DeployUseCase(
                case_id="deploy_repos_after_databases",
                title="Databases present — deploy repositories next",
                summary="Protected databases are already on MariaDB; repository checkout may still be needed.",
                commands=("./run.sh deploy repos --dry-run",),
            )
        )
    elif missing_repos:
        cases.append(
            DeployUseCase(
                case_id="deploy_repos_only",
                title="Deploy repositories only",
                summary="Configured repositories are missing on this host.",
                commands=(
                    "./run.sh deploy repos --dry-run",
                    "./run.sh deploy repos --from-github --dry-run",
                    "./run.sh deploy repos --from-usb --dry-run",
                ),
            )
        )

    github_ready = [c for c in missing_repos if c.source == "github"]
    usb_ready = [c for c in missing_repos if c.source == "usb_bundle"]
    if github_ready:
        cases.append(
            DeployUseCase(
                case_id="repos_from_github",
                title="Clone missing repositories from GitHub",
                summary="remote_url or operator-storage manifest metadata can drive git clone into configured paths.",
                commands=("./run.sh deploy repos --from-github --dry-run",),
            )
        )
    if usb_ready:
        cases.append(
            DeployUseCase(
                case_id="repos_from_usb",
                title="Restore missing repositories from operator-storage git bundles",
                summary="Offline or pinned restore from mercury_repo_backups bundle artifacts.",
                commands=("./run.sh deploy repos --from-usb --dry-run",),
            )
        )

    existing_repos = [c for c in candidates if c.exists_on_system]
    if existing_repos and missing_repos:
        names = ", ".join(c.display_name for c in existing_repos[:3])
        cases.append(
            DeployUseCase(
                case_id="partial_repo_checkout",
                title="Partial repository checkout",
                summary=f"Some repositories already exist ({names}); Mercury skips existing repos by default.",
                commands=("./run.sh deploy repos --dry-run",),
            )
        )

    if db_snapshot.import_count > 0:
        cases.append(
            DeployUseCase(
                case_id="live_database_deploy_ready",
                title="Database deployment plan is ready",
                summary=(
                    f"Review the dry-run plan for {db_snapshot.import_count} importable database(s), "
                    "then enable live actions to import verified operator-storage backups."
                ),
                commands=(
                    "./run.sh deploy db --dry-run",
                    "# set dry_run=false and live_actions_enabled=true, then:",
                    "./run.sh deploy db --execute",
                ),
            )
        )

    if repo_plan.planned_commands and not repo_plan.blockers:
        cases.append(
            DeployUseCase(
                case_id="live_repo_deploy_ready",
                title="Repository deployment plan is ready",
                summary="Review the dry-run plan, then enable live actions to clone missing repositories.",
                commands=(
                    "./run.sh deploy repos --dry-run",
                    "# set dry_run=false and live_actions_enabled=true, then:",
                    "./run.sh deploy repos --from-github --execute",
                ),
            )
        )

    if not cases:
        cases.append(
            DeployUseCase(
                case_id="no_deploy_action",
                title="No deployment action suggested",
                summary="Mercury did not detect a fresh-rebuild deployment scenario on this host.",
                commands=("./run.sh doctor",),
            )
        )
    return cases

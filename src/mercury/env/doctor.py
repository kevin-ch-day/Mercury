"""Mercury setup doctor — detect fresh-rebuild drift and print repair guidance."""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mercury.core.environment_status import (
    build_environment_status,
    recommended_next_step,
)
from mercury.core.path_permissions import chown_repair_command
from mercury.core.paths import REPO_ROOT, REPOS_LOCAL
from mercury.core.platform import detect_platform
from mercury.core.safety import BACKUP_KIND_FULL


@dataclass(frozen=True)
class SourceDatabaseCheck:
    name: str
    present: bool
    readable: bool
    detail: str


@dataclass
class DoctorReport:
    repo_root: Path
    current_user: str
    python_version: str
    platform_label: str
    config: object
    usb: object
    mariadb: object
    policy: object
    permission_checks: list = field(default_factory=list)
    source_databases: list[SourceDatabaseCheck] = field(default_factory=list)
    verified_backup_count: int = 0
    verified_backup_total: int = 0
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cleanup_suggestions: list[str] = field(default_factory=list)
    self_healed: list[str] = field(default_factory=list)
    recommended_next_step: str = "./run.sh menu"
    rebuild_complete: bool = False


def run_doctor(*, probe_database: bool = True, self_heal: bool = False) -> DoctorReport:
    """Collect environment diagnostics without privileged repairs."""
    env = build_environment_status(probe_database=probe_database, self_heal=self_heal)
    platform_info = detect_platform()
    report = DoctorReport(
        repo_root=REPO_ROOT,
        current_user=getpass.getuser(),
        python_version=sys.version.split()[0],
        platform_label=platform_info.support_label,
        config=env.config,
        usb=env.usb,
        mariadb=env.mariadb,
        policy=env.policy,
    )

    report.permission_checks = list(env.permission_checks)
    report.source_databases = _assess_source_databases(env.mariadb.connection_works is True)
    report.verified_backup_count, report.verified_backup_total = _count_verified_backups(env.policy)
    report.warnings = _collect_warnings(env, report)
    report.blockers = _collect_blockers(env, report)
    report.cleanup_suggestions = _collect_cleanup_suggestions(env)
    report.rebuild_complete = _rebuild_is_complete(report)
    report.recommended_next_step = _recommended_next_step(env, report)
    return report


def _rebuild_is_complete(report: DoctorReport) -> bool:
    if report.blockers:
        return False
    sources = [db for db in report.source_databases if db.name != "(discovery)"]
    if not sources:
        return False
    return all(db.present for db in sources)


def _recommended_next_step(env, report: DoctorReport) -> str:
    if report.blockers:
        if any("not writable" in blocker for blocker in report.blockers):
            return "./run.sh doctor --repair-plan"
        return recommended_next_step(env)
    if report.rebuild_complete:
        if env.policy.backup_execution_allowed():
            return "./run.sh backup all  # fresh backup of restored prod databases"
        return "./run.sh backup plan  # review backup plan once USB backup root is ready"
    missing_sources = [db for db in report.source_databases if not db.present and db.name != "(discovery)"]
    if (
        missing_sources
        and report.verified_backup_count == report.verified_backup_total
        and report.verified_backup_total > 0
        and env.mariadb.connection_works is True
    ):
        from mercury.database.mariadb.session import try_load_mariadb_config
        from mercury.deploy.privileges import deployment_grants_sufficient

        cfg = try_load_mariadb_config()
        if cfg is not None and not deployment_grants_sufficient(cfg)[0]:
            return "./run.sh doctor --repair-plan"
        return "./run.sh deploy system --dry-run"
    return recommended_next_step(env)


def _collect_cleanup_suggestions(env) -> list[str]:
    if env.mariadb.connection_works is not True:
        return []
    from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config
    from mercury.deploy.rebuild_status import detect_leftover_databases

    cfg = try_load_mariadb_config()
    if cfg is None:
        return []
    try:
        names = set(fetch_user_database_names(cfg))
    except Exception:
        return []
    return [cmd for _name, cmd in detect_leftover_databases(names)]


def build_repair_plan(report: DoctorReport) -> list[tuple[str, list[str]]]:
    """Return grouped shell/SQL repair commands (never executed automatically)."""
    sections: list[tuple[str, list[str]]] = []
    config = report.config
    usb = report.usb
    mariadb = report.mariadb
    user = report.current_user
    mount = usb.mount_path

    if config.missing_labels:
        sections.append(("Create local config", ["./run.sh config init"]))

    if not usb.mounted:
        sections.append(
            (
                "Mount Mercury USB (requires sudo)",
                [
                    f"sudo mkdir -p {mount}",
                    f"sudo mount LABEL=MERCURY_DATA_USB {mount}",
                    f"findmnt {mount}",
                ],
            )
        )
        sections.append(
            (
                "Optional: persist USB mount in /etc/fstab (review before applying)",
                [
                    f'LABEL=MERCURY_DATA_USB  {mount}  ext4  defaults,nofail  0  2',
                ],
            )
        )
    elif usb.mercury_layout_present and report.policy.config_path is None:
        sections.append(
            (
                "USB mounted — initialize Mercury config",
                [
                    "1. ./run.sh config init",
                    "2. ./run.sh doctor",
                    "3. ./run.sh db ping",
                ],
            )
        )

    chown_targets = _chown_repair_targets(report)
    if chown_targets:
        sections.append(
            (
                "Fix USB ownership (requires sudo)",
                [chown_repair_command(path) for path in chown_targets]
                + ["Optional one-shot helper: sudo ./scripts/repair-neptune.sh"],
            )
        )

    if mariadb.service_state == "inactive":
        sections.append(
            (
                "Start MariaDB service (requires sudo)",
                ["sudo systemctl enable --now mariadb", "systemctl is-active mariadb"],
            )
        )

    if mariadb.config_present and mariadb.connection_works is False:
        sections.append(
            (
                "Create MariaDB unix_socket user (requires sudo)",
                [
                    "sudo mariadb",
                    f"CREATE USER IF NOT EXISTS '{user}'@'localhost' IDENTIFIED VIA unix_socket;",
                    (
                        "GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, RELOAD, PROCESS, EVENT "
                        f"ON *.* TO '{user}'@'localhost';"
                    ),
                    "FLUSH PRIVILEGES;",
                ],
            )
        )
        sections.append(
            (
                "Update config/local.toml",
                [
                    "[mariadb]",
                    f'user = "{user}"',
                    "use_client = true",
                    'unix_socket = "/var/lib/mysql/mysql.sock"',
                ],
            )
        )
    elif not mariadb.config_present and config.local_toml_present:
        sections.append(
            (
                "Configure MariaDB in config/local.toml",
                [
                    "[mariadb]",
                    f'user = "{user}"',
                    "use_client = true",
                    'unix_socket = "/var/lib/mysql/mysql.sock"',
                ],
            )
        )

    if mariadb.config_present and mariadb.configured_user == "root" and user != "root":
        sections.append(
            (
                "Warning: root MariaDB user on Fedora",
                [
                    f'Mercury runs as {user}; MariaDB root via unix_socket requires OS root.',
                    f'Prefer user = "{user}" with a matching unix_socket MariaDB user.',
                ],
            )
        )

    missing_sources = [db.name for db in report.source_databases if not db.present and db.name != "(discovery)"]
    if (
        missing_sources
        and report.verified_backup_count == report.verified_backup_total
        and report.verified_backup_total > 0
    ):
        sections.append(
            (
                "Fresh rebuild — deploy databases from verified USB backups",
                [
                    "Verified USB backups exist; prod databases are not on MariaDB yet.",
                    "Combined plan: ./run.sh deploy system --dry-run",
                    "Database plan: ./run.sh deploy db --dry-run",
                    "Repository plan: ./run.sh deploy repos --dry-run",
                    "Menu lane: ./run.sh menu → option 8",
                ],
            )
        )
        if not REPOS_LOCAL.exists():
            sections.append(
                (
                    "Configure repository paths for this workstation",
                    ["./run.sh repo init-config", "Edit config/repos.toml and add remote_url for GitHub clones"],
                )
            )
        else:
            from mercury.repo import load_repo_definitions
            from mercury.repo.path_repair import repo_path_is_stale

            repos = load_repo_definitions()
            if any(repo_path_is_stale(repo.path) for repo in repos):
                sections.append(
                    (
                        "Rewrite stale repository paths for this workstation",
                        [
                            "./run.sh repo init-config --force",
                            "./run.sh deploy repos --dry-run",
                        ],
                    )
                )
            from mercury.repo.path_repair import web_repo_parent_dirs_needing_prep

            web_parents = web_repo_parent_dirs_needing_prep(repos)
            if web_parents:
                web_cmds: list[str] = []
                for parent in web_parents:
                    web_cmds.append(f"sudo mkdir -p {parent}")
                    web_cmds.append(f"sudo chown {user}:{user} {parent}")
                web_cmds.append("./run.sh deploy repos --dry-run")
                sections.append(
                    (
                        "Prepare web repository directories (requires sudo)",
                        web_cmds,
                    )
                )
        if mariadb.connection_works is True and mariadb.config_present:
            from mercury.database.mariadb.session import try_load_mariadb_config
            from mercury.deploy.privileges import deployment_grant_repair_sql, deployment_grants_sufficient

            cfg = try_load_mariadb_config()
            if cfg is not None:
                grants_ok, grant_detail = deployment_grants_sufficient(cfg)
                if not grants_ok:
                    sections.append(
                        (
                            "Grant MariaDB deployment privileges (requires sudo)",
                            deployment_grant_repair_sql(user),
                        )
                    )

    if not sections:
        sections.append(("No repairs suggested", ["Mercury doctor found no automatic repair steps."]))
    return sections


def _chown_repair_targets(report: DoctorReport) -> list[Path]:
    mount = report.usb.mount_path
    from mercury.core.setup_paths import MERCURY_USB_CHOWN_DIRS

    standard = [mount / dirname for dirname in MERCURY_USB_CHOWN_DIRS]
    targets: list[Path] = []
    seen: set[str] = set()
    for path in standard:
        key = str(path)
        if key in seen:
            continue
        check = next((c for c in report.permission_checks if c.path == path), None)
        if check is None and path.exists():
            from mercury.core.path_permissions import check_path_permission

            check = check_path_permission(path, label="USB path")
        if check is not None and check.needs_repair and path.exists():
            targets.append(path)
            seen.add(key)
    for check in report.permission_checks:
        if check.needs_repair and check.path.exists():
            key = str(check.path)
            if key not in seen and str(check.path).startswith(str(mount)):
                targets.append(check.path)
                seen.add(key)
    return targets


def _assess_source_databases(connection_ok: bool) -> list[SourceDatabaseCheck]:
    if not connection_ok:
        return []
    from mercury.backup.batch_runner import resolve_batch_sources
    from mercury.database.mariadb.errors import MariaDbLiveError
    from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config

    cfg = try_load_mariadb_config()
    if cfg is None:
        return []
    try:
        server_names = set(fetch_user_database_names(cfg))
    except (MariaDbLiveError, OSError) as exc:
        return [
            SourceDatabaseCheck(
                name="(discovery)",
                present=False,
                readable=False,
                detail=str(exc),
            )
        ]

    checks: list[SourceDatabaseCheck] = []
    for name in resolve_batch_sources(live=True):
        if name in server_names:
            checks.append(SourceDatabaseCheck(name=name, present=True, readable=True, detail="present"))
        else:
            checks.append(
                SourceDatabaseCheck(
                    name=name,
                    present=False,
                    readable=False,
                    detail="missing on server",
                )
            )
    return checks


def _count_verified_backups(policy) -> tuple[int, int]:
    from mercury.backup.batch_runner import resolve_batch_sources
    from mercury.backup.find_latest_backup import find_latest_backup_directory
    from mercury.backup.verification import verify_backup_artifacts

    if policy.config_path is None:
        return 0, 0
    sources = resolve_batch_sources(live=False)
    verified = 0
    for name in sources:
        backup_dir = find_latest_backup_directory(policy.backup_root, name)
        if backup_dir is None:
            continue
        if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
            continue
        result = verify_backup_artifacts(backup_dir, database=name)
        if result.verified and result.backup_kind == BACKUP_KIND_FULL:
            verified += 1
    return verified, len(sources)


def _collect_blockers(env, report: DoctorReport) -> list[str]:
    blockers: list[str] = []
    if env.primary_setup_blocker:
        blockers.append(env.primary_setup_blocker.rstrip("."))
    for label in env.repairable_blockers:
        if label in blockers:
            continue
        if (
            env.primary_setup_blocker
            and "USB Mercury paths not writable" in env.primary_setup_blocker
            and label.endswith("not writable")
        ):
            continue
        blockers.append(label)
    missing_sources = [db for db in report.source_databases if not db.present and db.name != "(discovery)"]
    backups_cover_sources = (
        report.verified_backup_count == report.verified_backup_total
        and report.verified_backup_total > 0
    )
    if missing_sources and not backups_cover_sources:
        for db_check in missing_sources:
            blockers.append(f"Source database missing on server: {db_check.name}")
    return blockers


def _collect_warnings(env, report: DoctorReport) -> list[str]:
    warnings: list[str] = []
    if not env.config.local_toml_present and env.usb.mercury_layout_present:
        warnings.append("USB target detected but local config is missing")
    if env.policy.backup_root_is_within_repo() and env.config.local_toml_present:
        warnings.append("Repo-local backup_root is dev-only and never safe for live mode")
    if env.mariadb.configured_user == "root" and report.current_user != "root":
        warnings.append(
            f'MariaDB user is "root" but Mercury runs as {report.current_user}; '
            "Fedora socket auth will fail unless run via sudo"
        )
    from mercury.repo import load_repo_definitions
    from mercury.repo.path_repair import repo_path_missing_detail, summarize_stale_repo_paths

    repos = load_repo_definitions()
    stale_summary = summarize_stale_repo_paths(repos)
    if stale_summary:
        warnings.append(stale_summary)
    for repo in repos:
        if detail := repo_path_missing_detail(repo):
            warnings.append(f"Repository path missing: {detail}")
    missing_sources = [db.name for db in report.source_databases if not db.present and db.name != "(discovery)"]
    if missing_sources:
        if (
            report.verified_backup_count == report.verified_backup_total
            and report.verified_backup_total > 0
        ):
            names = ", ".join(missing_sources)
            warnings.append(
                f"Fresh rebuild: {names} not on server yet; "
                f"{report.verified_backup_count} verified USB backup(s) available."
            )
        else:
            for name in missing_sources:
                warnings.append(f"Source database missing on server: {name}")
    return warnings

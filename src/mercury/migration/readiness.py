"""Aggregate existing Mercury evidence into a workstation-migration view."""

from __future__ import annotations

from mercury.migration.models import MigrationCheck, MigrationCheckState, MigrationReadinessReport


def _check(
    check_id: str,
    label: str,
    state: MigrationCheckState,
    summary: str,
    *,
    severity: str | None = None,
    evidence: tuple[str, ...] = (),
    action: str | None = None,
    command: str | None = None,
    blocking: bool = False,
) -> MigrationCheck:
    return MigrationCheck(
        id=check_id,
        label=label,
        state=state,
        severity=severity or state.value,
        summary=summary,
        evidence=evidence,
        recommended_action=action,
        recommended_command=command,
        blocking=blocking,
    )


def _repo_checks() -> tuple[MigrationCheck, MigrationCheck, MigrationCheck, MigrationCheck]:
    """Assess all configured worktrees without reading application contents."""
    from mercury.repo import inspect_repositories, load_repo_definitions
    from mercury.repo.config import RepoDefinition
    from mercury.migration.web_capture import WEB_REPO_KEYS, snapshot_status

    definitions = load_repo_definitions()
    definition_by_key = {repo.key: repo for repo in definitions}
    by_key = {status.key: status for status in inspect_repositories(definitions)}

    def worktree(key: str, label: str) -> MigrationCheck:
        status = by_key.get(key)
        if status is None or status.error or not status.exists:
            return _check(
                key + "_worktree", label, MigrationCheckState.ACTION_NEEDED,
                "Repository worktree is unavailable for migration assessment.",
                action="Validate the web repository checkout.", command="./run.sh repo status",
            )
        if status.dirty:
            snapshot_state, restore_checked = snapshot_status(RepoDefinition(key=key, display_name=label, path=status.path))
            if snapshot_state == "current" and restore_checked:
                return _check(
                    key + "_worktree", label, MigrationCheckState.PASS,
                    "Dirty worktree snapshot is current and restore checked.",
                    evidence=(str(status.path), "snapshot=current", "restore=checked"),
                )
            return _check(
                key + "_worktree", label, MigrationCheckState.ACTION_NEEDED,
                f"Dirty worktree snapshot is {snapshot_state}; Git bundles are insufficient.",
                evidence=(str(status.path), f"untracked={status.untracked_count}", f"snapshot={snapshot_state}"),
                action="Capture dirty web worktrees.",
                command="./run.sh migration capture-web",
            )
        return _check(
            key + "_worktree", label, MigrationCheckState.PASS,
            "Git worktree is clean; Git bundle coverage is available.", evidence=(str(status.path),),
        )

    erebus = worktree("erebus_web", "Erebus Web worktree")
    scytale = worktree("scytaledroid_web", "ScytaleDroid Web worktree")
    runtime = _check(
        "web_runtime_configuration", "Web runtime configuration", MigrationCheckState.PASS,
        "Runtime secrets (.env, credentials) are intentionally inventory-only and not packaged by Mercury.",
        evidence=("secrets_excluded=true",),
        action="Recreate runtime secrets on the destination via its secret-management process.",
        command="./run.sh repo status",
    )
    other_dirty = [
        status for key, status in by_key.items()
        if key not in WEB_REPO_KEYS and definition_by_key.get(key, RepoDefinition(key=key, display_name=key, path=status.path)).migration_scope
        and status.exists and status.dirty
    ]
    uncaptured_other: list[str] = []
    captured_other: list[str] = []
    stale_other: list[str] = []
    for status in other_dirty:
        snapshot_state, restore_checked = snapshot_status(
            RepoDefinition(key=status.key, display_name=status.display_name, path=status.path)
        )
        if snapshot_state == "current" and restore_checked:
            captured_other.append(status.key)
        else:
            uncaptured_other.append(status.key)
            if snapshot_state == "stale":
                stale_other.append(status.key)
    if uncaptured_other:
        bundles = _check(
            "repository_bundles", "Repository worktrees", MigrationCheckState.ACTION_NEEDED,
            f"{len(uncaptured_other)} dirty non-web repository worktree(s) need a restore-checked snapshot.",
            evidence=tuple(uncaptured_other + [f"stale={key}" for key in stale_other]), action="Capture dirty configured worktrees.",
            command="./run.sh migration capture-worktrees",
        )
    elif captured_other:
        bundles = _check(
            "repository_bundles", "Repository worktrees", MigrationCheckState.PASS,
            f"{len(captured_other)} dirty non-web repository worktree(s) have current restore-checked snapshots.",
            evidence=tuple(captured_other), command="./run.sh repo status",
        )
    else:
        bundles = _check(
            "repository_bundles", "Repository worktrees", MigrationCheckState.PASS,
            "No uncaptured dirty non-web worktrees were found.", command="./run.sh repo status",
        )
    return erebus, scytale, runtime, bundles


def build_migration_readiness(*, probe_database: bool = True, detailed: bool = True) -> MigrationReadinessReport:
    """Build evidence only; never copies, packages, mounts, or changes policy state."""
    from mercury.backup.status import build_backup_status_report
    from mercury.core.storage_roots import load_storage_config
    from mercury.storage.audit import find_mount_targets
    from mercury.storage.migrate_verify import verify_migration
    from mercury.storage.report import build_storage_status_report

    storage = build_storage_status_report()
    config = load_storage_config(warn_deprecated=False)
    checks: list[MigrationCheck] = []

    active = storage.primary if storage.primary.is_active_writer else storage.legacy
    writer_label = "HDD" if active.key == "primary" else "USB"
    active_state = MigrationCheckState.PASS if active.validation.ok else MigrationCheckState.BLOCKED
    checks.append(_check(
        "active_writer", "Active writer", active_state,
        f"{writer_label} · {'mounted' if active.validation.ok else 'not ready'}",
        evidence=(active.mount_path,), action="Repair the active writer mount." if not active.validation.ok else None,
        command="./run.sh storage validate" if not active.validation.ok else None, blocking=not active.validation.ok,
    ))
    mount_ok = storage.primary.validation.ok
    if config.cutover_complete:
        mount_state = (
            MigrationCheckState.PASS if mount_ok else MigrationCheckState.BLOCKED
        )
        mount_detail = (
            "Primary HDD mount validated (USB archive optional)."
            if mount_ok
            else "Primary HDD mount is not ready (USB archive is optional after cutover)."
        )
    else:
        mount_ok = storage.primary.validation.ok and storage.legacy.validation.ok
        mount_state = (
            MigrationCheckState.PASS if mount_ok else MigrationCheckState.BLOCKED
        )
        mount_detail = (
            "USB and HDD mounts validated."
            if mount_ok
            else "One or more configured storage mounts are not ready."
        )
    checks.append(_check(
        "storage_mounts", "Storage mounts", mount_state,
        mount_detail,
        command="./run.sh storage validate", blocking=mount_state == MigrationCheckState.BLOCKED,
    ))

    if not detailed:
        # Dashboard redraws must never hash backups, verify mirrors, scan worktrees,
        # collect SMART data, or run deployment preflight. Detailed evidence belongs
        # to explicit migration commands.
        checks.extend((
            _check("storage_mirror", "Storage mirror", MigrationCheckState.NOT_CHECKED,
                   f"Migration state: {config.migration_state.value} (not reverified on dashboard)."),
            _check("duplicate_primary_mount", "HDD duplicate mount", MigrationCheckState.NOT_CHECKED,
                   "Mount topology not refreshed on dashboard."),
            _check("database_backups", "Database backups", MigrationCheckState.NOT_CHECKED,
                   "Not reverified on dashboard."),
            _check("erebus_web_worktree", "Erebus Web worktree", MigrationCheckState.NOT_CHECKED,
                   "Not scanned on dashboard."),
            _check("web_runtime_configuration", "Web runtime configuration", MigrationCheckState.PASS,
                   "Runtime secrets are inventory-only (not rechecked on dashboard)."),
            _check("hdd_smart_health", "HDD SMART health", MigrationCheckState.NOT_CHECKED,
                   "Not rechecked on dashboard."),
            _check("destination_validation", "Destination workstation", MigrationCheckState.NOT_CHECKED,
                   "Destination workstation has not been validated.", blocking=True),
            _check("writer_cutover_implementation", "Writer cutover",
                   MigrationCheckState.PASS if config.cutover_complete else MigrationCheckState.BLOCKED,
                   "HDD is the active writer; USB is recovery archive only." if config.cutover_complete else "Writer cutover is not implemented.",
                   blocking=not config.cutover_complete),
        ))
        return MigrationReadinessReport(
            policy_state=config.migration_state.value,
            observed_mirror="not reverified",
            operator_phase=(
                "destination validation pending"
                if config.cutover_complete
                else "host capture pending"
            ),
            checks=tuple(checks),
        )

    if config.cutover_complete:
        from mercury.migration.generation import build_active_hdd_generation, read_archive_receipt, read_cutover_receipt
        active_generation = build_active_hdd_generation(config=config)
        receipt = read_cutover_receipt(config=config)
        archive = read_archive_receipt(config=config)
        checks.append(_check(
            "storage_mirror", "HDD package", MigrationCheckState.PASS,
            "HDD is authoritative after cutover; USB mirror comparison is historical only.",
            evidence=(f"active_hdd_generation={active_generation.generation[:12]}", f"cutover_receipt={'recorded' if receipt else 'missing'}"),
        ))
        checks.append(_check(
            "usb_archive_receipt", "USB archive receipt",
            MigrationCheckState.PASS if archive else MigrationCheckState.ACTION_NEEDED,
            "USB recovery archive receipt is recorded." if archive else "USB archive receipt has not been recorded.",
            action="Record immutable USB recovery-archive evidence." if not archive else None,
            command="./run.sh storage archive-receipt --execute" if not archive else None,
        ))
        observed_mirror = "historical cutover evidence; HDD authoritative"
    else:
        mirror = verify_migration(config=config)
        from mercury.migration.generation import build_usb_generation, read_verified_generation
        generation = build_usb_generation(config=config)
        verified_generation = read_verified_generation(config=config)
        generation_current = verified_generation == generation.generation
        mirror_state = MigrationCheckState.PASS if mirror.ok and generation_current else MigrationCheckState.ACTION_NEEDED
        observed_mirror = "current" if generation_current else "refresh required"
        checks.append(_check(
            "storage_mirror", "Storage mirror", mirror_state,
            "Current final package verified." if mirror.ok and generation_current else "USB package changed after the last recorded HDD verification.",
            evidence=(f"usb_generation={generation.generation[:12]}", f"hdd_verified={verified_generation[:12] if verified_generation else 'none'}"),
            action="Synchronize and verify the final USB package on HDD." if not generation_current else None,
            command="./run.sh storage migrate-plan" if not generation_current else None,
            blocking=not mirror.ok,
        ))
    targets = find_mount_targets(config.primary.filesystem_uuid)
    duplicate = len(targets) > 1
    checks.append(_check(
        "duplicate_primary_mount", "HDD duplicate mount",
        MigrationCheckState.WARNING if duplicate else MigrationCheckState.PASS,
        "HDD is mounted twice." if duplicate else "HDD has one mount target.",
        evidence=targets, action="Close desktop access and retain the canonical /mnt mount." if duplicate else None,
        command="./run.sh storage audit" if duplicate else None,
    ))

    backup = build_backup_status_report(live=probe_database)
    local_entries = [entry for entry in backup.entries if entry.protection_status != "absent"]
    local_verified = sum(entry.protection_status == "verified" for entry in local_entries)
    database_state = MigrationCheckState.PASS if local_entries and local_verified == len(local_entries) else MigrationCheckState.ACTION_NEEDED
    checks.append(_check(
        "database_backups", "Database backups", database_state,
        f"{local_verified} local sources verified." if database_state == MigrationCheckState.PASS else "Local database backup coverage needs attention.",
        evidence=tuple(entry.database for entry in local_entries),
        action="Run and verify required local database backups." if database_state != MigrationCheckState.PASS else None,
        command="./run.sh backup verify-all" if database_state != MigrationCheckState.PASS else None,
    ))
    obsidian = next((entry for entry in backup.entries if entry.database == "obsidiandroid_core_prod"), None)
    obsidian_absent = obsidian is not None and obsidian.protection_status == "absent"
    checks.append(_check(
        "obsidiandroid_core_prod", "ObsidianDroid database scope",
        MigrationCheckState.DECISION_NEEDED if obsidian_absent else MigrationCheckState.PASS,
        "Catalog source is absent from this host; disposition is required." if obsidian_absent else "Catalog source is present on this host.",
        evidence=tuple(obsidian.issues) if obsidian else (),
        action="Record whether this backup-only catalog belongs on the destination.",
        command="./run.sh db discover" if obsidian_absent else None,
    ))

    checks.extend(_repo_checks())
    from mercury.storage.smart_health import read_smart_health_record

    smart = read_smart_health_record(config=config)
    if smart and smart.get("overall_health_passed") is True:
        checks.append(_check(
            "hdd_smart_health", "HDD SMART health", MigrationCheckState.PASS,
            "Primary HDD SMART health PASSED and is recorded under .mercury_control/smart/.",
            evidence=(
                str(smart.get("block_device") or ""),
                str(smart.get("recorded_at_utc") or ""),
            ),
            command="./run.sh storage smart-health",
        ))
    elif smart:
        checks.append(_check(
            "hdd_smart_health", "HDD SMART health", MigrationCheckState.ACTION_NEEDED,
            "SMART evidence exists but overall health did not PASS.",
            action="Inspect SMART evidence and replace the disk if health failed.",
            command="./run.sh storage smart-health",
            severity="CHECK",
        ))
    else:
        checks.append(_check(
            "hdd_smart_health", "HDD SMART health", MigrationCheckState.NOT_CHECKED,
            "SMART health has not been recorded by Mercury.",
            action="Record HDD SMART health.",
            command="./run.sh storage smart-health --execute",
            severity="CHECK",
        ))
    checks.append(_check(
        "destination_validation", "Destination workstation", MigrationCheckState.NOT_CHECKED,
        "Destination workstation has not been validated.", action="Validate the destination workstation.",
        command="./run.sh deploy system --dry-run", blocking=True,
    ))
    if config.cutover_complete:
        checks.append(_check(
            "writer_cutover_implementation", "Writer cutover", MigrationCheckState.PASS,
            "HDD is the active writer; USB is recovery archive only.",
        ))
    else:
        checks.append(_check(
            "writer_cutover_implementation", "Writer cutover", MigrationCheckState.BLOCKED,
            "Writer cutover is not implemented.", action="Complete host capture and destination validation before cutover work.",
            command="./run.sh storage cutover-plan", blocking=True,
        ))

    open_capture = any(
        check.unresolved
        for check in checks
        if check.id in {"erebus_web_worktree", "scytaledroid_web_worktree", "repository_bundles"}
    )
    phase = "host capture pending" if open_capture else "destination validation pending"
    return MigrationReadinessReport(
        policy_state=config.migration_state.value,
        observed_mirror=observed_mirror,
        operator_phase=phase,
        checks=tuple(checks),
    )

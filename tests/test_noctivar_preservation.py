import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_repo_lane import _git, _git_output, _make_repo, _mirror_setup

from mercury.repo.offline_clone import execute_offline_clone_plan
from mercury.repo.recovery import recover_repository
from mercury.repo.snapshot import snapshot_uncommitted
from mercury.storage import verified_volume as volume

DISK = "780 41 8:17 / /mnt/MERCURY_DATA rw - ext4 /dev/sdb1 rw\n"
AUTOFS = "41 46 0:114 / /mnt/MERCURY_DATA rw - autofs systemd-1 rw\n"


@pytest.mark.parametrize(
    "record",
    [
        DISK.replace("rw", "ro"),
        DISK.replace("ext4", "xfs"),
        DISK.replace("/dev/sdb1", "untrusted"),
        AUTOFS,
        "",
    ],
)
def test_invalid_volume_states(record):
    with pytest.raises(ValueError):
        volume.require_disk(
            "/mnt/MERCURY_DATA",
            "uuid",
            mountinfo=record,
            device_stat=lambda p: SimpleNamespace(
                st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 17)
            ),
        )


def test_mount_source_device_and_job_headroom(tmp_path, monkeypatch):
    def device(path):
        return SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 17 if "by-uuid" in path else 18)
        )

    with pytest.raises(ValueError, match="mount source differs"):
        volume.require_disk(
            "/mnt/MERCURY_DATA", "uuid", mountinfo=DISK, device_stat=device
        )
    monkeypatch.setattr(
        "mercury.core.storage_roots.load_storage_config",
        lambda: SimpleNamespace(
            space_policy=SimpleNamespace(minimum_free_bytes=10, minimum_free_percent=10)
        ),
    )
    monkeypatch.setattr(
        volume.shutil, "disk_usage", lambda p: SimpleNamespace(total=100, free=11)
    )
    with pytest.raises(ValueError, match="headroom"):
        volume.require_headroom(tmp_path, 2)
    volume.require_headroom(tmp_path, 1)


def test_approved_activation_rechecks_physical_identity(monkeypatch):
    monkeypatch.setattr(
        volume, "mount_records", lambda *a: [("0:114", "autofs", "systemd-1", {"rw"})]
    )
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **k: (
            "UUID=uuid /mnt/MERCURY_DATA ext4 rw,x-systemd.automount 0 2\n"
            if str(p) == "/etc/fstab"
            else original(p, *a, **k)
        ),
    )
    calls = []
    monkeypatch.setattr(
        volume.subprocess, "run", lambda argv, **kw: calls.append((argv, kw))
    )
    monkeypatch.setattr(volume, "require_disk", lambda *a: calls.append("verified"))
    volume.activate_disk("/mnt/MERCURY_DATA", "uuid")
    assert calls[0][0] == ["stat", "--", "/mnt/MERCURY_DATA/."]
    assert calls[0][1]["timeout"] == 20
    assert calls[-1] == "verified"


def test_failed_activation_never_admits(monkeypatch):
    monkeypatch.setattr(
        volume, "mount_records", lambda *a: [("0:1", "autofs", "systemd-1", {"rw"})]
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *a, **k: "UUID=uuid /mnt/MERCURY_DATA ext4 rw,x-systemd.automount 0 2\n",
    )
    monkeypatch.setattr(
        volume.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(ValueError, match="activation failed"):
        volume.activate_disk("/mnt/MERCURY_DATA", "uuid")


def test_destination_symlink_and_overlap_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(volume, "require_disk", lambda *a: None)
    (tmp_path / "escape").symlink_to("/tmp")
    with pytest.raises(ValueError, match="symlink"):
        volume.require_destination(
            str(tmp_path), "uuid", tmp_path / "escape" / "output"
        )
    with (
        volume.exclusive_lock(tmp_path / "lock"),
        pytest.raises(ValueError, match="Another backup"),
        volume.exclusive_lock(tmp_path / "lock"),
    ):
        pass


def test_recover_dirty_modes_links_deletions_and_tamper(tmp_path):
    repo = _make_repo(tmp_path, "source")
    (repo / "removed").write_text("old")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "removed")
    (repo / "removed").unlink()
    (repo / "README.md").write_text("new content\n")
    (repo / "script.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "script.sh").chmod(0o755)
    (repo / "relative").symlink_to("README.md")
    (repo / "outside").symlink_to("/etc/passwd")
    before = _git_output(repo, "status", "--porcelain")
    snapshot = tmp_path / "snapshot"
    record = snapshot_uncommitted(repo, snapshot)
    restored = tmp_path / "restored"
    commit = _git_output(repo, "rev-parse", "HEAD")
    out = recover_repository(
        repo,
        restored,
        commit=commit,
        snapshot=snapshot,
        manifest_sha256=record["manifest_sha256"],
    )
    assert out["status"] == "restored_verified"
    assert (restored / "README.md").read_text() == "new content\n"
    assert (restored / "script.sh").stat().st_mode & 0o777 == 0o755
    assert os.readlink(restored / "relative") == "README.md"
    assert not (restored / "removed").exists()
    assert out["excluded_files"] == ["outside"]
    assert before == _git_output(repo, "status", "--porcelain")
    with pytest.raises(ValueError, match="new isolated"):
        recover_repository(repo, restored, commit=commit)
    (snapshot / "tracked.patch").write_text("corrupt")
    with pytest.raises(ValueError, match="digest"):
        recover_repository(
            repo,
            tmp_path / "bad",
            commit=commit,
            snapshot=snapshot,
            manifest_sha256=record["manifest_sha256"],
        )


def test_same_path_mutation_refuses_snapshot(tmp_path, monkeypatch):
    import mercury.repo.snapshot as module

    repo = _make_repo(tmp_path, "source")
    (repo / "README.md").write_text("before\n")
    original = module.git_bytes

    def changed(source, *args, **kwargs):
        output = original(source, *args, **kwargs)
        if "diff" in args and "--binary" in args:
            (repo / "README.md").write_text("after\n")
        return output

    monkeypatch.setattr(module, "git_bytes", changed)
    with pytest.raises(ValueError, match="changed"):
        snapshot_uncommitted(repo, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


def test_relatime_read_does_not_invalidate_snapshot(tmp_path, monkeypatch):
    import mercury.repo.snapshot as module

    repo = _make_repo(tmp_path, "source")
    (repo / "README.md").write_text("retained\n")
    original = module.os.fstat
    counter = iter(range(100))

    def fstat(fd):
        x = original(fd)
        return SimpleNamespace(
            st_dev=x.st_dev,
            st_ino=x.st_ino,
            st_size=x.st_size,
            st_mtime_ns=x.st_mtime_ns,
            st_ctime_ns=x.st_ctime_ns,
            st_mode=x.st_mode,
            st_atime=next(counter),
        )

    monkeypatch.setattr(module.os, "fstat", fstat)
    record = snapshot_uncommitted(repo, tmp_path / "snapshot")
    assert record["saved_files"] == 1


def test_snapshot_special_file_is_excluded_without_opening(tmp_path, monkeypatch):
    import mercury.repo.snapshot as module

    repo = _make_repo(tmp_path, "source")
    os.mkfifo(repo / "input-pipe")
    # Git normally ignores FIFOs; exercise a candidate discovered by a caller.
    monkeypatch.setattr(module, "dirty_paths", lambda source: ([], ["input-pipe"]))
    record = snapshot_uncommitted(repo, tmp_path / "snapshot")
    manifest = json.loads((Path(record["path"]) / "snapshot.json").read_text())
    assert manifest["excluded_files"] == ["input-pipe"]


def test_disappearing_mount_keeps_previous_success(tmp_path, monkeypatch):
    _source, plan = _mirror_setup(tmp_path, monkeypatch)
    first = execute_offline_clone_plan(plan())
    old = first.root / "offline_repo_sync_receipt.json"
    digest = hashlib.sha256(old.read_bytes()).hexdigest()
    calls = []

    def guard():
        calls.append(1)
        if len(calls) > 2:
            raise ValueError("disk disappeared")

    with pytest.raises(ValueError, match="disappeared"):
        execute_offline_clone_plan(plan(), guard=guard)
    assert hashlib.sha256(old.read_bytes()).hexdigest() == digest
    assert any(
        json.loads(p.read_text())["status"] == "incomplete"
        for p in first.root.glob("generations/*/STATE.json")
    )


def test_noctivar_is_exact_backup_only_source():
    from mercury.backup.backup_runner import (
        BackupExecutionError,
        assert_not_production_restore_target,
    )
    from mercury.database.core import classify_database, is_active_backup_source

    assert classify_database("noctivar").backup_source
    assert is_active_backup_source("noctivar")
    assert classify_database("noctivar_other").manual_review
    with pytest.raises(BackupExecutionError):
        assert_not_production_restore_target("noctivar")


@pytest.mark.parametrize("fault", [None, "interrupt", "mutation"])
def test_protected_generation_publication_and_failure_retention(
    tmp_path, monkeypatch, fault
):
    import gzip

    from mercury.backup import protected_generation as module
    from mercury.backup.backup_runner import BackupExecutionError, execute_backup
    from mercury.backup.database_fingerprint import encode_row
    from mercury.core.execution_policy import ExecutionPolicy
    from mercury.database.mariadb.config import MariaDbConnectionConfig

    assert encode_row([None]) != encode_row(["None"])
    assert encode_row([b"x"]) != encode_row(["x"])
    backup_root = tmp_path / "mercury_backups"
    backup_root.mkdir()
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=backup_root,
        config_path=tmp_path / "config.toml",
        usb_mount=tmp_path,
        allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr(
        ExecutionPolicy, "backup_environment_refusal", lambda self: None
    )
    monkeypatch.setattr(
        module,
        "load_storage_config",
        lambda: SimpleNamespace(
            active_write_root=SimpleNamespace(
                mount_path=tmp_path, filesystem_uuid="synthetic"
            )
        ),
    )
    monkeypatch.setattr(module, "activate_disk", lambda *a: None)
    monkeypatch.setattr(module, "require_destination", lambda *a: None)
    monkeypatch.setattr(module, "require_headroom", lambda *a: None)
    monkeypatch.setattr(
        "mercury.database.mariadb.session.fetch_user_database_names",
        lambda *a: ["noctivar"],
    )
    prints = iter([{"synthetic": 1}, {"synthetic": 2 if fault == "mutation" else 1}])
    monkeypatch.setattr(
        "mercury.backup.database_fingerprint.fingerprint", lambda *a: next(prints)
    )

    def runner(argv, env, output, config):
        with gzip.open(output, "wb") as f:
            f.write(b"-- synthetic backup\n")
        if fault == "interrupt":
            raise KeyboardInterrupt()

    def staged(*args, **kwargs):
        kwargs["live"] = False
        return execute_backup(*args, **kwargs, dump_runner=runner)

    monkeypatch.setattr(module, "execute_backup", staged)
    config = MariaDbConnectionConfig(
        host="localhost",
        user="synthetic",
        unix_socket="/synthetic.sock",
        use_client=True,
    )
    if fault:
        with pytest.raises(
            KeyboardInterrupt if fault == "interrupt" else BackupExecutionError
        ):
            module.execute_protected_backup("noctivar", policy=policy, config=config)
        assert not list(backup_root.glob("*/*/*/manifest.json"))
        states = list((tmp_path / "mercury_backup_incomplete").glob("*/STATE.json"))
        assert (
            len(states) == 1 and json.loads(states[0].read_text())["status"] == "failed"
        )
        assert list((tmp_path / "mercury_backup_incomplete").rglob("*.gz*"))
    else:
        result = module.execute_protected_backup(
            "noctivar", policy=policy, config=config
        )
        published = Path(result.backup_directory_path)
        assert published.is_relative_to(backup_root)
        assert published.parts[-3] == "2026-11-04" or len(published.parts[-3]) == 10
        assert (
            json.loads((published / "COMPLETION.json").read_text())["status"]
            == "complete"
        )
        assert json.loads((published / "manifest.json").read_text())["verified"] is True


def test_bounded_metadata_copy_restore_tamper_and_payload_refusal(tmp_path):
    from mercury.backup.evidence import preserve_files, restore_files

    source = tmp_path / "PLAN.json"
    source.write_text('{"research":"frozen"}\n')
    source.chmod(0o400)
    plan = {
        "files": [
            {
                "source": str(source),
                "name": "research/PLAN.json",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
        "scope": "synthetic selected metadata",
        "max_bytes": 1024,
    }
    generation = tmp_path / "generation"
    record = preserve_files(plan, generation, guard=lambda p: None)
    restored = tmp_path / "restored"
    result = restore_files(
        generation, restored, manifest_sha256=record["manifest_sha256"]
    )
    assert result["files"] == 1
    assert (restored / "research/PLAN.json").read_bytes() == source.read_bytes()
    assert (restored / "research/PLAN.json").stat().st_mode & 0o777 == 0o400
    with pytest.raises(ValueError, match="already exists"):
        preserve_files(plan, generation, guard=lambda p: None)
    (generation / "files.tar.gz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="digest"):
        restore_files(
            generation, tmp_path / "bad", manifest_sha256=record["manifest_sha256"]
        )
    plan["files"][0]["name"] = "sample.apk"
    plan["files"][0]["source"] = str(tmp_path / "sample.apk")
    with pytest.raises(ValueError, match="APK"):
        preserve_files(plan, tmp_path / "refused", guard=lambda p: None)
    assert not (tmp_path / "refused").exists()


def test_health_mount_unavailable_never_traverses_backup_tree(tmp_path, monkeypatch):
    from mercury.backup import health

    monkeypatch.setattr(
        health,
        "load_storage_config",
        lambda: SimpleNamespace(
            active_write_root=SimpleNamespace(
                mount_path=tmp_path, filesystem_uuid="expected"
            )
        ),
    )
    monkeypatch.setattr(health, "_unit_state", lambda: {"ActiveState": "failed"})
    monkeypatch.setattr(
        health,
        "require_disk",
        lambda *a: (_ for _ in ()).throw(ValueError("autofs only")),
    )
    monkeypatch.setattr(
        health,
        "read_json",
        lambda *a: pytest.fail("must not traverse unverified backup paths"),
    )
    result = health.build_health()
    assert result["physical_mount"] == "unavailable_or_invalid"
    assert result["service"]["ActiveState"] == "failed"

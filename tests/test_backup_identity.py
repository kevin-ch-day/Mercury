"""Verification identity is explicit; historical evidence stays unknown."""
import json
import pytest
from mercury.backup.manifest import BackupManifest
from mercury.backup.verification import collect_verification_identity, verify_backup_directory
from mercury.database.mariadb.config import MariaDbConnectionConfig


def test_collect_identity_uses_readonly_query(monkeypatch):
    seen = []
    def query(config, sql):
        seen.append(sql)
        return ["actual@localhost", "db-host"]
    monkeypatch.setattr("mercury.database.mariadb.session.readonly_row", query)
    result = collect_verification_identity(MariaDbConnectionConfig(host="localhost", user="requested"))
    assert result == {"requested_user": "requested", "authenticated_user": "actual@localhost",
                      "server_hostname": "db-host"}
    assert seen == ["SELECT CURRENT_USER(), @@hostname"]


@pytest.mark.parametrize("identity,match", [({}, None),
    ({"requested_user": "u", "authenticated_user": "u@localhost", "server_hostname": "host"}, True),
    ({"requested_user": "u", "authenticated_user": "other@localhost", "server_hostname": "host"}, False)])
def test_manifest_identity_old_and_new_shapes(tmp_path, identity, match):
    data = dict(backup_id="x", database="android_permission_intel", backup_kind="full",
                created_at="2026-10-22T00:00:00Z", dump_file="dump.sql.gz", source_role="shared_authority",
                **identity)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    result = verify_backup_directory(tmp_path)
    assert result.identity_matches is match
    assert result.authenticated_user == identity.get("authenticated_user")
    assert path.read_bytes() == before
    assert BackupManifest.model_validate(data).authenticated_user == identity.get("authenticated_user")


def test_missing_live_identity_fails_closed(monkeypatch):
    monkeypatch.setattr("mercury.database.mariadb.session.readonly_row", lambda *_: "")
    with pytest.raises(ValueError, match="unavailable"):
        collect_verification_identity(MariaDbConnectionConfig(host="localhost", user="u"))


def test_verified_stamp_preserves_legacy_manifest_shape(tmp_path, monkeypatch):
    from mercury.backup.verification import BackupVerificationResult
    path = tmp_path / "manifest.json"
    data = dict(backup_id="old", database="android_permission_intel", backup_kind="full",
                created_at="2026-10-22T00:00:00Z", dump_file="dump.sql.gz", source_role="shared_authority")
    path.write_text(json.dumps(data))
    result = BackupVerificationResult(backup_id="old", database="android_permission_intel",
        backup_kind="full", manifest_path=str(path), verified=True)
    monkeypatch.setattr("mercury.backup.verification.verify_backup_artifacts", lambda *a, **k: result)
    verify_backup_directory(tmp_path, update_manifest=True)
    assert json.loads(path.read_text()) == {**data, "verified": True}


def test_live_backup_manifest_records_verification_identity(monkeypatch, tmp_path):
    from mercury.backup.backup_runner import execute_backup
    from mercury.backup.content_contract import BackupObjectInventory
    from tests.test_backup_execute import _live_policy, _fake_mariadb_config
    monkeypatch.setattr("mercury.backup.backup_runner.fetch_live_object_inventory", lambda *_: BackupObjectInventory())
    monkeypatch.setattr("mercury.backup.backup_runner.view_dumpability_error", lambda *_: None)
    monkeypatch.setattr("mercury.database.mariadb.session.readonly_row", lambda *_: ["actual@localhost", "db-host"])
    import gzip
    def fake_dump(argv, env, path, config):
        path.write_bytes(gzip.compress(b"-- Dump completed on 2026-10-22\n"))
    cfg = _fake_mariadb_config()
    result = execute_backup("android_permission_intel", "full", execute=True, live=True,
        server_names={"android_permission_intel"}, policy=_live_policy(tmp_path),
        mariadb_config=cfg, dump_runner=fake_dump)
    assert result.executed
    assert result.manifest.requested_user == cfg.user
    assert result.manifest.authenticated_user == "actual@localhost"
    assert result.manifest.server_hostname == "db-host"

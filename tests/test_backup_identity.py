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
        return "actual@localhost\tdb-host"
    monkeypatch.setattr("mercury.database.mariadb.session.readonly_scalar", query)
    result = collect_verification_identity(MariaDbConnectionConfig(host="localhost", user="requested"))
    assert result == {"requested_user": "requested", "authenticated_user": "actual@localhost",
                      "server_hostname": "db-host"}
    assert seen == ["SELECT CONCAT(CURRENT_USER(), CHAR(9), @@hostname)"]


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
    monkeypatch.setattr("mercury.database.mariadb.session.readonly_scalar", lambda *_: "")
    with pytest.raises(ValueError, match="unavailable"):
        collect_verification_identity(MariaDbConnectionConfig(host="localhost", user="u"))

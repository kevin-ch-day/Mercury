"""Configured label precedence and warning-only mismatch diagnostics."""
import json
from dataclasses import replace
from types import SimpleNamespace
from mercury.core.storage_roles import DEFAULT_PRIMARY_LABEL, primary_storage_label
from mercury.core.storage_roots import default_storage_config, load_storage_config
from mercury.env.doctor import primary_label_warnings


def test_label_config_and_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / "local.toml"
    monkeypatch.setenv("MERCURY_LOCAL_CONFIG", str(path))
    monkeypatch.delenv("MERCURY_PRIMARY_LABEL", raising=False)
    assert primary_storage_label() == DEFAULT_PRIMARY_LABEL == "MERCURY_DATA"
    path.write_text('[storage.primary]\nlabel="CUSTOM"\n')
    assert primary_storage_label() == "CUSTOM"
    monkeypatch.setenv("MERCURY_PRIMARY_LABEL", "OVERRIDE")
    assert load_storage_config(local_config=path).primary.label == "OVERRIDE"
    assert primary_storage_label() == "OVERRIDE"


def test_doctor_label_mismatch_is_warning_only():
    cfg = default_storage_config()
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        # Literal old physical label intentionally exercises mismatch handling.
        return SimpleNamespace(returncode=0, stdout=json.dumps({"filesystems": [
            {"label": "MERCURY_DATA_V2", "fstype": "ext4"}]}))
    warnings = primary_label_warnings(config=cfg, runner=run)
    assert len(warnings) == 1
    assert f"sudo e2label /dev/disk/by-uuid/{cfg.primary.filesystem_uuid} {DEFAULT_PRIMARY_LABEL}" in warnings[0]
    assert calls == [["findmnt", "--json", "-S", f"UUID={cfg.primary.filesystem_uuid}", "-o", "LABEL,FSTYPE"]]
    assert cfg == default_storage_config()
    matching = replace(cfg, primary=replace(cfg.primary, label="MERCURY_DATA_V2"))
    assert primary_label_warnings(config=matching, runner=run) == []


def test_capture_validation_uses_label_override(monkeypatch):
    from mercury.migration.erebus_capture.storage_preflight import StorageFacts, validate_storage, EXPECTED_UUID, EXPECTED_MOUNT
    monkeypatch.setenv("MERCURY_PRIMARY_LABEL", "OVERRIDE")
    facts = StorageFacts("/dev/x1", "/dev/x", "ext4", "OVERRIDE", EXPECTED_UUID, EXPECTED_MOUNT, "rw", 100, True, True)
    assert validate_storage(facts).label == "OVERRIDE"


def test_automount_uses_label_override(monkeypatch, tmp_path):
    from mercury.storage.detach_wizard import detect_desktop_automount
    monkeypatch.setenv("MERCURY_PRIMARY_LABEL", "OVERRIDE")
    mount = tmp_path / "user" / "OVERRIDE"
    mount.mkdir(parents=True)
    assert detect_desktop_automount(media_root=tmp_path) == [str(mount)]

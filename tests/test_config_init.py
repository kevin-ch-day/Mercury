"""Tests for config init."""

from pathlib import Path

from mercury.config_init import init_local_config


def test_init_creates_files(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    example_db = config_dir / "databases.example.toml"
    example_db.write_text('[databases]\nfoo_prod = { host = "h", port = 1 }\n', encoding="utf-8")
    example_local = config_dir / "local.example.toml"
    example_local.write_text("[mercury]\nmode = 'seed'\n", encoding="utf-8")

    local_db = config_dir / "databases.toml"
    local_local = config_dir / "local.toml"

    monkeypatch.setattr("mercury.config_init.DATABASES_EXAMPLE", example_db)
    monkeypatch.setattr("mercury.config_init.DATABASES_LOCAL", local_db)
    monkeypatch.setattr("mercury.config_init.LOCAL_EXAMPLE", example_local)
    monkeypatch.setattr("mercury.config_init.LOCAL_CONFIG", local_local)

    results = init_local_config()
    assert local_db.exists()
    assert local_local.exists()
    assert any("created" in r for r in results)

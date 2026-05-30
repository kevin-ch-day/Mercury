"""Smoke tests that core modules import without circular-import errors."""


def test_import_cli() -> None:
    import mercury.cli  # noqa: F401


def test_import_database_module() -> None:
    from mercury.database import discover_from_config

    inventory = discover_from_config(include_catalog=False)
    assert inventory.count >= 1


def test_database_discover_uses_paths_not_legacy_config() -> None:
    import inspect

    import mercury.database.discovery.config as module

    source = inspect.getsource(module)
    assert "mercury.database.core" in source
    assert "from mercury.config import DATABASES" not in source

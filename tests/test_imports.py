"""Smoke tests that core modules import without circular-import errors."""


def test_import_cli() -> None:
    import mercury.cli  # noqa: F401


def test_import_canonical_packages() -> None:
    from mercury.logging import configure_logging, get_logger
    from mercury.logging.events import log_menu_action
    from mercury.core.safety import BACKUP_KIND_FULL

    assert BACKUP_KIND_FULL == "full"
    configure_logging()
    assert get_logger("mercury.test")


def test_legacy_shims_still_import() -> None:
    """Top-level re-exports remain for external callers."""
    from mercury.safety import BACKUP_KIND_FULL  # noqa: F401
    from mercury.logging_engine import configure_logging  # noqa: F401
    from mercury.display_screen import write_fields  # noqa: F401
    from mercury.menu import run_menu  # noqa: F401


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

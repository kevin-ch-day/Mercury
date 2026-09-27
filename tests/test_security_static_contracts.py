"""Repository-wide static security invariants for production Python."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from mercury.config.settings import _mariadb_config_status


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "mercury"


def test_production_code_has_no_optimization_sensitive_assertions() -> None:
    """Safety checks must remain active when Python runs with ``-O``."""
    violations: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        )

    assert violations == [], "production assert statements: " + ", ".join(violations)


def test_config_status_does_not_expose_credential_environment_name() -> None:
    config = SimpleNamespace(
        use_client=False,
        unix_socket=None,
        password_env="MERCURY_TEST_DATABASE_PASSWORD",
    )

    status = _mariadb_config_status(config)

    assert status == "ready (password via environment variable)"
    assert config.password_env not in status

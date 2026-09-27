from __future__ import annotations

import stat

import pytest

from mercury.core.artifact_permissions import (
    ensure_private_directory,
    restrict_artifact_file,
    write_private_text,
)


def test_private_artifact_permissions_are_operator_only(tmp_path) -> None:
    directory = ensure_private_directory(tmp_path / "artifacts")
    artifact = directory / "backup.sql.gz"
    artifact.write_bytes(b"backup")
    restrict_artifact_file(artifact)

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


def test_private_artifact_permissions_refuse_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text("safe", encoding="utf-8")
    link = tmp_path / "artifact"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="non-regular"):
        restrict_artifact_file(link)


def test_private_text_write_is_atomic_and_operator_only(tmp_path) -> None:
    path = tmp_path / "private" / "evidence.json"

    write_private_text(path, '{"status":"PASS"}\n')

    assert path.read_text(encoding="utf-8") == '{"status":"PASS"}\n'
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_private_text_write_refuses_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text("safe", encoding="utf-8")
    link = tmp_path / "artifact"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        write_private_text(link, "replacement")
    assert target.read_text(encoding="utf-8") == "safe"

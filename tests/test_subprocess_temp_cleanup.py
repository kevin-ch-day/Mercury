"""Subprocess test environments must not leak unmanaged /tmp directories."""
from contextlib import ExitStack
from pathlib import Path

import pytest

from tests import conftest


def test_existing_environment_does_not_allocate_scratch(monkeypatch, tmp_path):
    monkeypatch.setenv(conftest.ENV_STATE_ROOT, str(tmp_path / 'state'))
    monkeypatch.setenv('MERCURY_LOCAL_CONFIG', str(tmp_path / 'local.toml'))

    def unexpected_allocation(*args, **kwargs):
        pytest.fail('Existing isolation paths must not allocate temporary directories')

    monkeypatch.setattr(conftest.tempfile, 'TemporaryDirectory', unexpected_allocation)
    env = conftest.subprocess_env()
    assert env[conftest.ENV_STATE_ROOT] == str(tmp_path / 'state')
    assert env['MERCURY_LOCAL_CONFIG'] == str(tmp_path / 'local.toml')


@pytest.mark.parametrize('failed', [False, True])
def test_fallback_directories_cleaned_at_session_end(monkeypatch, tmp_path, failed):
    monkeypatch.delenv(conftest.ENV_STATE_ROOT, raising=False)
    monkeypatch.delenv('MERCURY_LOCAL_CONFIG', raising=False)
    monkeypatch.setattr(conftest.tempfile, 'tempdir', str(tmp_path))
    with ExitStack() as stack:
        monkeypatch.setattr(conftest, '_SUBPROCESS_TEMP_DIRS', stack)
        env = conftest.subprocess_env()
        roots = [Path(env[conftest.ENV_STATE_ROOT]), Path(env['MERCURY_LOCAL_CONFIG']).parent]
        assert all(root.is_dir() for root in roots)
        assert not Path(env['MERCURY_LOCAL_CONFIG']).exists()
        try:
            for root in roots:
                (root / 'result.txt').write_text('test output')
            if failed:
                raise RuntimeError('simulated test failure')
        except RuntimeError:
            pass
        finally:
            conftest.pytest_sessionfinish()
        assert all(not root.exists() for root in roots)


def test_explicit_environment_overrides_still_work(tmp_path):
    env = conftest.subprocess_env({conftest.ENV_STATE_ROOT: str(tmp_path), 'MERCURY_LOCAL_CONFIG': ''})
    assert env[conftest.ENV_STATE_ROOT] == str(tmp_path)
    assert 'MERCURY_LOCAL_CONFIG' not in env

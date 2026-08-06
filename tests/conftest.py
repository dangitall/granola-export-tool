"""Shared pytest fixtures."""

import pytest

from granola_export.paths import CONFIG_DIR_ENV


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Point this tool's credential store at a throwaway dir for every test.

    get_token_from_local() now mirrors refresh tokens into our own config
    dir; without this, tests would write to the real ~/.config/granola-export.
    """
    config_dir = tmp_path / "granola-export-config"
    monkeypatch.setenv(CONFIG_DIR_ENV, str(config_dir))
    return config_dir

"""Tests for platform path resolution."""

from unittest.mock import patch
from pathlib import Path

from granola_export.paths import (
    get_granola_data_dir,
    get_default_cache_path,
    get_token_path,
    CACHE_FILENAME,
    TOKEN_FILENAME,
)


class TestGetGranolaDataDir:
    @patch("granola_export.paths.platform.system", return_value="Darwin")
    def test_macos(self, _mock):
        result = get_granola_data_dir()
        assert "Library" in str(result)
        assert str(result).endswith("Granola")

    @patch("granola_export.paths.platform.system", return_value="Windows")
    def test_windows(self, _mock):
        result = get_granola_data_dir()
        assert "AppData" in str(result)
        assert str(result).endswith("Granola")

    @patch("granola_export.paths.platform.system", return_value="Linux")
    def test_linux(self, _mock):
        result = get_granola_data_dir()
        assert ".config" in str(result)
        assert str(result).endswith("Granola")

    @patch("granola_export.paths.platform.system", return_value="FreeBSD")
    def test_unsupported_platform(self, _mock):
        try:
            get_granola_data_dir()
            assert False, "Should have raised NotImplementedError"
        except NotImplementedError:
            pass


class TestDerivedPaths:
    """Verify that cache and token paths are built from the data dir."""

    @patch("granola_export.paths.platform.system", return_value="Darwin")
    def test_cache_path_uses_data_dir(self, _mock):
        cache_path = get_default_cache_path()
        data_dir = get_granola_data_dir()
        assert cache_path == data_dir / CACHE_FILENAME

    @patch("granola_export.paths.platform.system", return_value="Darwin")
    def test_token_path_uses_data_dir(self, _mock):
        token_path = get_token_path()
        data_dir = get_granola_data_dir()
        assert token_path == data_dir / TOKEN_FILENAME

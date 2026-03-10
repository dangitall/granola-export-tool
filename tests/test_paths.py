"""Tests for platform path resolution."""

from unittest.mock import patch

import pytest

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
        assert result.parts[-3:] == ("Library", "Application Support", "Granola")

    @patch("granola_export.paths.platform.system", return_value="Windows")
    def test_windows(self, _mock):
        result = get_granola_data_dir()
        assert result.parts[-3:] == ("AppData", "Roaming", "Granola")

    @patch("granola_export.paths.platform.system", return_value="Linux")
    def test_linux(self, _mock):
        result = get_granola_data_dir()
        assert result.parts[-2:] == (".config", "Granola")

    @patch("granola_export.paths.platform.system", return_value="FreeBSD")
    def test_unsupported_platform_falls_back_to_xdg(self, _mock):
        """Unknown Unix platforms should fall back to ~/.config, not raise."""
        result = get_granola_data_dir()
        assert result.parts[-2:] == (".config", "Granola")


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

    @patch("granola_export.paths.platform.system", return_value="Windows")
    def test_cache_path_windows(self, _mock):
        cache_path = get_default_cache_path()
        assert cache_path.name == CACHE_FILENAME
        assert "AppData" in str(cache_path)


class TestBackwardCompatReExport:
    """Guard against accidental removal of the re-export from cache.py."""

    def test_get_default_cache_path_importable_from_cache_module(self):
        from granola_export.cache import get_default_cache_path as fn

        assert callable(fn)

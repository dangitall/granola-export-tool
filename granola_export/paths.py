"""
Platform-specific path resolution for Granola data files.

Centralizes all Granola file path logic so each file name is defined
once and every module resolves paths through the same function.
"""

import platform
from pathlib import Path


def get_granola_data_dir() -> Path:
    """
    Return the platform-specific Granola application data directory.

    Returns:
        Path to the Granola data directory.

    Raises:
        NotImplementedError: If the platform is not supported.
    """
    system = platform.system()

    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Granola"
    elif system == "Windows":
        return Path.home() / "AppData" / "Roaming" / "Granola"
    elif system == "Linux":
        return Path.home() / ".config" / "Granola"
    else:
        raise NotImplementedError(f"Unsupported platform: {system}")


# Well-known file names within the Granola data directory.
CACHE_FILENAME = "cache-v3.json"
TOKEN_FILENAME = "supabase.json"


def get_default_cache_path() -> Path:
    """Return the full path to the Granola local cache file."""
    return get_granola_data_dir() / CACHE_FILENAME


def get_token_path() -> Path:
    """Return the full path to the Granola auth token file."""
    return get_granola_data_dir() / TOKEN_FILENAME

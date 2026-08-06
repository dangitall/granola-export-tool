"""
Platform-specific path resolution for Granola data files.

Centralizes all Granola file path logic so each file name is defined
once and every module resolves paths through the same function.
"""

import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger(__name__)


def get_granola_data_dir() -> Path:
    """
    Return the platform-specific Granola application data directory.

    On unrecognised Unix-like platforms, falls back to ``~/.config/Granola``
    (the XDG default) so the tool is best-effort usable everywhere.

    Returns:
        Path to the Granola data directory.
    """
    system = platform.system()

    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Granola"
    elif system == "Windows":
        return Path.home() / "AppData" / "Roaming" / "Granola"
    else:
        # Linux and any other Unix-like OS — use XDG default.
        if system != "Linux":
            logger.warning(
                "Unsupported platform %r; falling back to ~/.config/Granola",
                system,
            )
        return Path.home() / ".config" / "Granola"


# Well-known file names within the Granola data directory.
CACHE_FILENAME = "cache-v6.json"
TOKEN_FILENAME = "supabase.json"
ACCOUNTS_FILENAME = "stored-accounts.json"

# Environment override + file name for *our own* credential store (see
# get_config_dir). Kept separate from Granola's data dir so it survives
# Granola encrypting/removing its plaintext token files.
CONFIG_DIR_ENV = "GRANOLA_EXPORT_CONFIG_DIR"
CREDENTIALS_FILENAME = "credentials.json"


def get_config_dir() -> Path:
    """Return this tool's own config directory (not Granola's).

    This is where we persist a refresh token so exports keep working even
    after Granola stops writing plaintext credentials to disk. It is
    deliberately independent of :func:`get_granola_data_dir`.

    Resolution order:
      1. ``GRANOLA_EXPORT_CONFIG_DIR`` if set (used by tests and power users).
      2. ``XDG_CONFIG_HOME/granola-export`` if ``XDG_CONFIG_HOME`` is set.
      3. Platform default: ``%APPDATA%\\granola-export`` on Windows,
         ``~/.config/granola-export`` everywhere else.
    """
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "granola-export"

    if platform.system() == "Windows":
        return Path.home() / "AppData" / "Roaming" / "granola-export"
    return Path.home() / ".config" / "granola-export"


def get_credentials_path() -> Path:
    """Return the full path to this tool's persisted credential store."""
    return get_config_dir() / CREDENTIALS_FILENAME


def get_default_cache_path() -> Path:
    """Return the full path to the Granola local cache file."""
    return get_granola_data_dir() / CACHE_FILENAME


def get_token_path() -> Path:
    """Return the full path to the legacy Granola auth token file."""
    return get_granola_data_dir() / TOKEN_FILENAME


def get_accounts_path() -> Path:
    """Return the full path to Granola's stored-accounts.json file.

    Granola v7+ writes auth tokens here; older versions used supabase.json.
    """
    return get_granola_data_dir() / ACCOUNTS_FILENAME

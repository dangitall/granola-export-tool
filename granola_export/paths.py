"""
Platform-specific path resolution for Granola data files.

Centralizes all Granola file path logic so each file name is defined
once and every module resolves paths through the same function.
"""

import json
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
STATE_FILENAME = "state.json"

# Where `api-export` writes and the read commands (list, search, show,
# stats, export, check) look for its output. See get_default_data_dir.
DATA_DIR_ENV = "GRANOLA_EXPORT_DATA_DIR"
DEFAULT_DATA_DIRNAME = "granola-api-export"


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


def get_state_path() -> Path:
    """Return the path to this tool's small state file (last export dir)."""
    return get_config_dir() / STATE_FILENAME


def get_default_data_dir() -> Path:
    """Return the api-export output directory the read commands should use.

    Resolution order:
      1. ``GRANOLA_EXPORT_DATA_DIR`` if set.
      2. The directory the most recent ``api-export`` wrote to, so a cron
         job exporting to a custom ``-o`` needs no extra configuration.
      3. ``~/granola-api-export`` (api-export's own default).
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()

    try:
        with open(get_state_path()) as f:
            last = json.load(f).get("last_export_dir")
        if isinstance(last, str) and last:
            return Path(last)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    return Path.home() / DEFAULT_DATA_DIRNAME


def record_export_dir(path: Path) -> None:
    """Remember ``path`` as the latest api-export output (best effort)."""
    state_path = get_state_path()
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if not isinstance(state, dict):
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state["last_export_dir"] = str(Path(path).expanduser().resolve())
    tmp_path = state_path.with_suffix(f".{os.getpid()}.tmp")
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(state, indent=2))
        os.replace(tmp_path, state_path)
    except OSError as e:
        logger.debug(f"Could not record export dir in {state_path}: {e}")
        tmp_path.unlink(missing_ok=True)

"""
Shared utility functions for granola_export.
"""

from typing import Optional


def safe_filename(name: Optional[str], max_length: int = 50) -> str:
    """
    Convert a string to a filesystem-safe filename.

    Args:
        name: The original string (can be None).
        max_length: Maximum filename length.

    Returns:
        A filesystem-safe filename.
    """
    if not name:
        name = "Untitled"

    # Replace unsafe characters with underscores
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)

    # Collapse multiple consecutive underscores
    while "__" in safe:
        safe = safe.replace("__", "_")

    # Trim to max length and strip leading/trailing whitespace and separators
    return safe[:max_length].strip(" _-")

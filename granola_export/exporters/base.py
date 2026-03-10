"""
Base exporter class defining the export interface.
"""

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..cache import GranolaCache
from ..models import ExportResult


def safe_filename(name: Optional[str], max_length: int = 50) -> str:
    """
    Convert a string to a filesystem-safe filename.

    Args:
        name: The original string (None becomes "Untitled").
        max_length: Maximum filename length.

    Returns:
        A filesystem-safe filename.
    """
    if not name:
        name = "Untitled"
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    safe = re.sub(r"_+", "_", safe)  # collapse runs of underscores
    return safe[:max_length].strip(" _-")


class Exporter(ABC):
    """
    Minimal interface that all exporters satisfy.

    This exists so code that only needs to call export() can accept
    either cache-based or API-based exporters without lying about
    inheritance.
    """

    @abstractmethod
    def export(self) -> ExportResult:
        """
        Export data to the target format.

        Returns:
            ExportResult with details about the export.
        """


class BaseExporter(Exporter):
    """
    Abstract base for cache-based exporters (JSON, Markdown, CSV, HTML).

    Subclasses receive a loaded GranolaCache and must implement export().
    """

    format_name: str = "base"
    file_extension: str = ""

    def __init__(
        self,
        cache: GranolaCache,
        output_dir: Path,
        include_transcripts: bool = True,
        include_raw: bool = False,
    ):
        """
        Initialize the exporter.

        Args:
            cache: The loaded GranolaCache instance.
            output_dir: Directory to write exported files.
            include_transcripts: Whether to include transcript data.
            include_raw: Whether to include raw/original data.
        """
        self.cache = cache
        self.output_dir = Path(output_dir)
        self.include_transcripts = include_transcripts
        self.include_raw = include_raw

    def prepare_output_dir(self) -> None:
        """Create the output directory if it doesn't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _safe_filename(self, name: str, max_length: int = 50) -> str:
        """Convenience wrapper around the module-level safe_filename."""
        return safe_filename(name, max_length)

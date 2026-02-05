"""
Base exporter class defining the export interface.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..cache import GranolaCache
from ..models import ExportResult
from ..utils import safe_filename


class BaseExporter(ABC):
    """
    Abstract base class for all exporters.

    Subclasses must implement the export() method to handle
    their specific format.
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

    @abstractmethod
    def export(self) -> ExportResult:
        """
        Export data to the target format.

        Returns:
            ExportResult with details about the export.
        """
        pass

    def _safe_filename(self, name: Optional[str], max_length: int = 50) -> str:
        """Convert a string to a filesystem-safe filename."""
        return safe_filename(name, max_length)

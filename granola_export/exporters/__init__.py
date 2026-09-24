"""
Export modules for different output formats.

Supports JSON, Markdown, CSV, HTML, and direct API export formats.
"""

from .api_exporter import APIExporter, APIExportError, AuthenticationError
from .base import BaseExporter, Exporter, safe_filename
from .csv_exporter import CSVExporter
from .html_exporter import HTMLExporter
from .json_exporter import JSONExporter
from .markdown_exporter import MarkdownExporter

__all__ = [
    "Exporter",
    "BaseExporter",
    "safe_filename",
    "JSONExporter",
    "MarkdownExporter",
    "CSVExporter",
    "HTMLExporter",
    "APIExporter",
    "APIExportError",
    "AuthenticationError",
]


def get_exporter(format_name: str) -> type[BaseExporter]:
    """
    Get the exporter class for a given format.

    Args:
        format_name: The export format (json, markdown, csv, html).

    Returns:
        The exporter class. The API exporter isn't listed: it fetches from
        the network rather than reading a source (see ``api-export``).

    Raises:
        ValueError: If the format is not supported.
    """
    exporters = {
        "json": JSONExporter,
        "markdown": MarkdownExporter,
        "md": MarkdownExporter,
        "csv": CSVExporter,
        "html": HTMLExporter,
    }

    format_lower = format_name.lower()
    if format_lower not in exporters:
        supported = ", ".join(sorted(set(exporters.keys())))
        raise ValueError(f"Unknown format '{format_name}'. Supported: {supported}")

    return exporters[format_lower]

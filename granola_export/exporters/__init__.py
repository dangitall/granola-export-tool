"""
Export modules for different output formats.

Supports JSON, Markdown, CSV, HTML, and direct API export formats.
"""

from .base import BaseExporter, Exporter, safe_filename
from .json_exporter import JSONExporter
from .markdown_exporter import MarkdownExporter
from .csv_exporter import CSVExporter
from .html_exporter import HTMLExporter
from .api_exporter import APIExporter

__all__ = [
    "Exporter",
    "BaseExporter",
    "safe_filename",
    "JSONExporter",
    "MarkdownExporter",
    "CSVExporter",
    "HTMLExporter",
    "APIExporter",
]


def get_exporter(format_name: str) -> type:
    """
    Get the exporter class for a given format.

    Args:
        format_name: The export format (json, markdown, csv, html, api).

    Returns:
        The exporter class.

    Raises:
        ValueError: If the format is not supported.
    """
    exporters = {
        "json": JSONExporter,
        "markdown": MarkdownExporter,
        "md": MarkdownExporter,
        "csv": CSVExporter,
        "html": HTMLExporter,
        "api": APIExporter,
    }

    format_lower = format_name.lower()
    if format_lower not in exporters:
        supported = ", ".join(sorted(set(exporters.keys())))
        raise ValueError(f"Unknown format '{format_name}'. Supported: {supported}")

    return exporters[format_lower]

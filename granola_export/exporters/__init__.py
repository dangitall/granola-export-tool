"""
Export modules for different output formats.

Supports JSON, Markdown, CSV, and HTML export formats.
"""

from .base import BaseExporter
from .json_exporter import JSONExporter
from .markdown_exporter import MarkdownExporter
from .csv_exporter import CSVExporter
from .html_exporter import HTMLExporter

__all__ = [
    "BaseExporter",
    "JSONExporter",
    "MarkdownExporter",
    "CSVExporter",
    "HTMLExporter",
]


def get_exporter(format_name: str) -> type:
    """
    Get the exporter class for a given format.

    Args:
        format_name: The export format (json, markdown, csv, html).

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
    }

    format_lower = format_name.lower()
    if format_lower not in exporters:
        supported = ", ".join(sorted(set(exporters.keys())))
        raise ValueError(f"Unknown format '{format_name}'. Supported: {supported}")

    return exporters[format_lower]

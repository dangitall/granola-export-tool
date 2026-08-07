"""Tests for MarkdownExporter frontmatter generation."""

from unittest.mock import MagicMock

import pytest

from granola_export.exporters.markdown_exporter import MarkdownExporter
from granola_export.models import Document, Meeting


def _exporter(tmp_path):
    """A MarkdownExporter over a stubbed cache (frontmatter enabled)."""
    return MarkdownExporter(cache=MagicMock(), output_dir=tmp_path)


def _title_line(exporter, title):
    md = exporter._meeting_to_markdown(Meeting(document=Document(id="d1", title=title)))
    return next(ln for ln in md.splitlines() if ln.startswith("title:"))


class TestFrontmatterTitleEscaping:
    def test_plain_title_unquoted_content(self, tmp_path):
        assert _title_line(_exporter(tmp_path), "Weekly Sync") == 'title: "Weekly Sync"'

    def test_embedded_quotes_are_escaped(self, tmp_path):
        # A double quote must become \" so it can't close the YAML string early.
        assert (
            _title_line(_exporter(tmp_path), 'He said "hi"')
            == 'title: "He said \\"hi\\""'
        )

    def test_backslashes_are_escaped(self, tmp_path):
        # A lone backslash must become \\ (and not swallow the closing quote).
        assert (
            _title_line(_exporter(tmp_path), "path\\to\\thing")
            == 'title: "path\\\\to\\\\thing"'
        )

    def test_escaped_title_is_valid_yaml(self, tmp_path):
        """The emitted frontmatter title round-trips through a YAML parser."""
        yaml = pytest.importorskip("yaml")
        title = 'Tricky: "quotes" and a \\ slash'
        line = _title_line(_exporter(tmp_path), title)
        assert yaml.safe_load(line) == {"title": title}

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


class TestExport:
    def test_same_day_same_title_meetings_both_written(self, tmp_path):
        """Recurring same-titled meetings on one day must not overwrite."""
        cache = MagicMock()
        cache.meetings.return_value = [
            Meeting(
                document=Document.from_dict(
                    doc_id, {"title": "Standup", "created_at": created}
                )
            )
            for doc_id, created in [
                ("aaaaaaaa-1", "2026-01-05T15:00:00Z"),
                ("bbbbbbbb-2", "2026-01-05T17:00:00Z"),
                # Mixed epoch-ms and missing dates must sort without error.
                ("cccccccc-3", 1767628800000),
                ("dddddddd-4", None),
            ]
        ]

        result = MarkdownExporter(cache=cache, output_dir=tmp_path).export()

        assert result.success, result.errors
        notes = [p for p in tmp_path.glob("*.md") if p.name != "INDEX.md"]
        assert len(notes) == 4


class TestFrontmatterYaml:
    def test_newline_in_title_and_participants_stay_valid_yaml(self, tmp_path):
        yaml = pytest.importorskip("yaml")
        meeting = Meeting(
            document=Document(
                id="d1", title="Line one\nline: two", participants=["O'Brien", 'A "B"']
            )
        )
        md = _exporter(tmp_path)._meeting_to_markdown(meeting)
        front = md.split("---\n")[1]

        data = yaml.safe_load(front)
        assert data["title"] == "Line one\nline: two"
        assert data["participants"] == ["O'Brien", 'A "B"']

    def test_control_characters_stay_valid_yaml(self, tmp_path):
        yaml = pytest.importorskip("yaml")
        title = "del\x7f nel\x85 c1\x9b"
        meeting = Meeting(document=Document(id="d1", title=title, participants=[title]))
        front = _exporter(tmp_path)._meeting_to_markdown(meeting).split("---\n")[1]

        data = yaml.safe_load(front)
        assert data["title"] == title
        assert data["participants"] == [title]

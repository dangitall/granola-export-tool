"""Tests for the HTML report's embedded data."""

import json
import re
from unittest.mock import MagicMock

from granola_export.exporters.html_exporter import HTMLExporter


def test_script_close_tag_in_content_cannot_break_out():
    exporter = HTMLExporter(cache=MagicMock(), output_dir="unused")
    evil = "</script><img src=x onerror=alert(1)>"
    html = exporter._generate_html(
        [{"title": evil, "panels": []}],
        stats={
            "total_meetings": 1,
            "with_transcripts": 0,
            "total_words": 0,
            "date_range": "",
        },
    )

    script = re.search(r"const meetings = (.*?);\n", html, re.S)
    assert script, "embedded data not found"
    assert "</script>" not in script.group(1)
    assert json.loads(script.group(1))[0]["title"] == evil


def test_comment_script_sequence_cannot_swallow_page():
    """ "<!--<script>" would put the parser in its double-escaped state."""
    exporter = HTMLExporter(cache=MagicMock(), output_dir="unused")
    text = "<!--<script> a & b \u2028"
    html = exporter._generate_html(
        [{"title": text, "panels": []}],
        stats={
            "total_meetings": 1,
            "with_transcripts": 0,
            "total_words": 0,
            "date_range": "",
        },
    )

    data = re.search(r"const meetings = (.*?);\n", html, re.S).group(1)
    assert "<" not in data and "&" not in data and "\u2028" not in data
    assert json.loads(data)[0]["title"] == text

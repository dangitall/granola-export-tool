"""Tests for ProseMirror/HTML -> Markdown conversion."""

from granola_export.prosemirror import html_to_markdown, to_markdown


def _text(text, *marks):
    return {"type": "text", "text": text, "marks": [{"type": m} for m in marks]}


class TestToMarkdown:
    def test_headings_paragraphs_and_marks(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 3}, "content": [_text("Plan")]},
                {
                    "type": "paragraph",
                    "content": [_text("Ship "), _text("today", "bold")],
                },
                {"type": "horizontalRule"},
            ],
        }

        assert to_markdown(doc) == "### Plan\n\nShip **today**\n\n---"

    def test_nested_lists_and_tasks(self):
        item = lambda text, *children: {  # noqa: E731
            "type": "listItem",
            "content": [{"type": "paragraph", "content": [_text(text)]}, *children],
        }
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        item("a", {"type": "bulletList", "content": [item("b")]})
                    ],
                },
                {
                    "type": "taskList",
                    "content": [
                        {
                            "type": "taskItem",
                            "attrs": {"checked": True},
                            "content": [
                                {"type": "paragraph", "content": [_text("done")]}
                            ],
                        }
                    ],
                },
            ],
        }

        assert to_markdown(doc) == "- a\n  - b\n\n- [x] done"

    def test_links(self):
        link = {
            "type": "text",
            "text": "site",
            "marks": [{"type": "link", "attrs": {"href": "https://x.test"}}],
        }
        doc = {"type": "doc", "content": [{"type": "paragraph", "content": [link]}]}

        assert to_markdown(doc) == "[site](https://x.test)"

    def test_unknown_nodes_keep_their_text(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "callout",
                    "content": [{"type": "paragraph", "content": [_text("hi")]}],
                }
            ],
        }

        assert to_markdown(doc) == "hi"

    def test_strings_and_none(self):
        assert to_markdown(None) == ""
        assert to_markdown("  plain  ") == "plain"
        assert to_markdown("<p>html</p>") == "html"


class TestHtmlToMarkdown:
    def test_summary_html(self):
        html = "<h3>Topic</h3>\n<ul>\n<li>one <strong>bold</strong></li>\n<li>two</li>\n</ul>"

        assert html_to_markdown(html) == "### Topic\n- one **bold**\n- two"

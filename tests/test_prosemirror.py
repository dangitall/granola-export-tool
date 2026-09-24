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


def _para(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _list(kind, *items):
    return {
        "type": kind,
        "content": [{"type": "listItem", "content": list(i)} for i in items],
    }


class TestListStructure:
    def test_multi_paragraph_item_stays_in_list(self):
        doc = {
            "type": "doc",
            "content": [
                _list(
                    "orderedList",
                    [_para("a"), _list("bulletList", [_para("b"), _para("b2")])],
                    [_para("c")],
                )
            ],
        }

        assert to_markdown(doc) == "1. a\n   - b\n\n     b2\n2. c"

    def test_emphasis_keeps_whitespace_outside_markers(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        _text("bold ", "bold"),
                        _text("x"),
                        _text("k", "bold", "code"),
                        {"type": "mention", "attrs": {"label": "@Ann"}},
                    ],
                }
            ],
        }

        assert to_markdown(doc) == "**bold** x**`k`**@Ann"

    def test_plain_text_with_angle_brackets_is_not_html(self):
        assert to_markdown("<3 this > that") == "<3 this > that"


class TestHtmlLists:
    def test_li_wrapping_p_keeps_bullets(self):
        html = "<ul><li><p>one</p></li><li><p>two</p><ul><li><p>sub</p></li></ul></li></ul>"

        assert html_to_markdown(html) == "- one\n- two\n  - sub"

    def test_ordered_lists_and_links(self):
        html = (
            '<ol><li>a<ol><li>b</li></ol></li><li>c <a href="https://x">l</a></li></ol>'
        )

        assert html_to_markdown(html) == "1. a\n   1. b\n2. c [l](https://x)"

    def test_second_paragraph_in_item_is_indented(self):
        assert html_to_markdown("<ul><li><p>p1</p><p>p2</p></li></ul>") == "- p1\n  p2"

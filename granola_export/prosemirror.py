"""
Convert Granola note content to Markdown.

Granola stores notes and AI summary panels as ProseMirror JSON documents
(``{"type": "doc", "content": [...]}``). Documents scraped from a web share
page carry HTML instead. Both are converted to Markdown so every exporter,
``show`` and ``search`` work with plain text.
"""

import re
from html.parser import HTMLParser
from typing import Any

# Content counts as HTML only if it opens with a tag Granola emits, so
# plain text like "<3 ... >" isn't fed to the HTML parser.
_HTML_START = re.compile(
    r"\s*<(h[1-6]|p|ul|ol|li|div|br|hr|strong|em|b|i|a|span|blockquote)[\s/>]",
    re.IGNORECASE,
)


def to_markdown(node: Any) -> str:
    """Render a ProseMirror node (or HTML/plain string) as Markdown.

    Unknown node types render their children, so new Granola node types
    degrade to plain text instead of disappearing.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return html_to_markdown(node) if _looks_like_html(node) else node.strip()
    if not isinstance(node, dict):
        return ""
    return _join_blocks(node.get("content") or []).strip()


def _looks_like_html(text: str) -> bool:
    return bool(_HTML_START.match(text))


_LIST_TYPES = ("bulletList", "orderedList", "taskList")


def _render_blocks(nodes: list) -> list[str]:
    return [_render_block(n) for n in nodes if isinstance(n, dict)]


def _join_blocks(nodes: list) -> str:
    return "\n\n".join(b for b in _render_blocks(nodes) if b)


def _render_block(node: dict) -> str:
    kind = node.get("type")
    children = node.get("content") or []
    attrs = node.get("attrs") or {}

    if kind == "paragraph":
        return _render_inline(children)
    if kind == "heading":
        level = attrs.get("level")
        level = min(max(level, 1), 6) if isinstance(level, int) else 1
        # A heading is one line; a hard break inside it becomes a space.
        text = _render_inline(children).replace("  \n", " ")
        return f"{'#' * level} {text}"
    if kind in _LIST_TYPES:
        return _render_list(node)
    if kind == "blockquote":
        inner = _join_blocks(children)
        return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
    if kind == "codeBlock":
        text = "".join(c.get("text", "") for c in children if isinstance(c, dict))
        return f"```\n{text}\n```"
    if kind == "horizontalRule":
        return "---"
    if kind == "text":
        return _render_inline([node])
    # Unknown container: keep its text.
    return _join_blocks(children)


def _render_list(node: dict) -> str:
    """Render a list; item bodies are indented under their marker.

    Everything after an item's first line (further paragraphs, nested
    lists) is indented by the marker's width so Markdown keeps it inside
    the item rather than ending the list.
    """
    kind = node.get("type")
    start = (node.get("attrs") or {}).get("start")
    start = start if isinstance(start, int) else 1
    items = []
    number = start
    for item in node.get("content") or []:
        if not isinstance(item, dict):
            continue
        if kind == "orderedList":
            marker = f"{number}."
            number += 1
        elif item.get("type") == "taskItem":
            checked = (item.get("attrs") or {}).get("checked")
            marker = "- [x]" if checked else "- [ ]"
        else:
            marker = "-"
        # Tight lists: a nested list follows its parent line directly;
        # further paragraphs are separated by a blank line.
        body = ""
        for child in item.get("content") or []:
            if not isinstance(child, dict):
                continue
            part = _render_block(child)
            if not part:
                continue
            if not body:
                body = part
            elif child.get("type") in _LIST_TYPES:
                body += "\n" + part
            else:
                body += "\n\n" + part
        indent = " " * (len(marker) + 1)
        lines = body.split("\n")
        first = f"{marker} {lines[0]}".rstrip()
        rest = [f"{indent}{line}" if line else "" for line in lines[1:]]
        items.append("\n".join([first, *rest]))
    return "\n".join(items)


def _wrap(text: str, marker: str) -> str:
    """Wrap ``text`` in ``marker``, keeping edge whitespace outside it.

    ``**bold **`` isn't bold in Markdown; ``**bold** `` is.
    """
    core = text.strip()
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{marker}{core}{marker}{trail}"


def _render_inline(nodes: list) -> str:
    out = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        if kind == "hardBreak":
            out.append("  \n")
            continue
        if kind != "text":
            children = node.get("content") or []
            if children:
                out.append(_render_inline(children))
            else:
                # Leaf inline nodes such as mentions carry a label.
                label = (node.get("attrs") or {}).get("label")
                if isinstance(label, str):
                    out.append(label)
            continue
        text = node.get("text") or ""
        marks = {
            m.get("type"): m for m in node.get("marks") or [] if isinstance(m, dict)
        }
        # Code first: emphasis markers inside backticks would be literal.
        if "code" in marks:
            text = _wrap(text, "`")
        if "italic" in marks:
            text = _wrap(text, "*")
        if "bold" in marks:
            text = _wrap(text, "**")
        href = (marks.get("link", {}).get("attrs") or {}).get("href")
        out.append(f"[{text}]({href})" if href else text)
    return "".join(out)


class _HTMLToMarkdown(HTMLParser):
    """Minimal HTML -> Markdown for Granola's summary/share-page HTML.

    Handles headings, paragraphs, nested ordered/unordered lists (including
    ``<li><p>...</p></li>``), emphasis and links.
    """

    _BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        # One entry per open <ul>/<ol>: [kind, next number].
        self.lists: list[list] = []
        # Marker for the <li> whose first line hasn't been emitted yet.
        self.pending_marker: str | None = None
        self.heading = ""
        self.hrefs: list[str | None] = []

    def _indent(self) -> str:
        """Indentation for lines inside the innermost open list item."""
        width = 0
        for kind, number in self.lists:
            width += len(f"{number - 1}.") + 1 if kind == "ol" else 2
        return " " * width

    def _flush(self) -> None:
        text = "".join(self.current).strip()
        self.current = []
        if not text:
            return
        if self.pending_marker is not None:
            outer = (
                self._indent()[: -(len(self.pending_marker) + 1)] if self.lists else ""
            )
            line = f"{outer}{self.pending_marker} {text}"
            self.pending_marker = None
        elif self.lists:
            line = f"{self._indent()}{text}"
        else:
            line = f"{self.heading}{text}"
        self.heading = ""
        self.lines.append(line)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("ul", "ol"):
            self._flush()
            self.lists.append([tag, 1])
        elif tag == "li":
            self._flush()
            if self.lists:
                kind, number = self.lists[-1]
                if kind == "ol":
                    self.pending_marker = f"{number}."
                    self.lists[-1][1] = number + 1
                else:
                    self.pending_marker = "-"
            else:
                self.pending_marker = "-"
        elif tag in self._BLOCKS:
            self._flush()
            if tag.startswith("h") and tag[1:].isdigit():
                self.heading = "#" * int(tag[1:]) + " "
            elif tag == "hr":
                self.lines.append("---")
        elif tag in ("strong", "b"):
            self.current.append("**")
        elif tag in ("em", "i"):
            self.current.append("*")
        elif tag == "a":
            self.hrefs.append(dict(attrs).get("href"))
            self.current.append("[")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("ul", "ol"):
            self._flush()
            if self.lists:
                self.lists.pop()
        elif tag == "li" or tag in self._BLOCKS:
            self._flush()
        elif tag in ("strong", "b"):
            self.current.append("**")
        elif tag in ("em", "i"):
            self.current.append("*")
        elif tag == "a" and self.hrefs:
            href = self.hrefs.pop()
            self.current.append(f"]({href})" if href else "]")

    def handle_data(self, data: str) -> None:
        self.current.append(data.replace("\n", " "))

    def result(self) -> str:
        self._flush()
        return "\n".join(self.lines)


def html_to_markdown(html: str) -> str:
    """Convert simple HTML (headings, lists, paragraphs) to Markdown."""
    parser = _HTMLToMarkdown()
    parser.feed(html)
    parser.close()
    return parser.result().strip()

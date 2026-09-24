"""
Convert Granola note content to Markdown.

Granola stores notes and AI summary panels as ProseMirror JSON documents
(``{"type": "doc", "content": [...]}``). Documents scraped from a web share
page carry HTML instead. Both are converted to Markdown so every exporter,
``show`` and ``search`` work with plain text.
"""

from html.parser import HTMLParser
from typing import Any


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
    blocks = _render_blocks(node.get("content") or [], depth=0)
    return "\n\n".join(b for b in blocks if b).strip()


def _looks_like_html(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("<") and ">" in stripped


def _render_blocks(nodes: list, depth: int) -> list[str]:
    return [_render_block(n, depth) for n in nodes if isinstance(n, dict)]


def _render_block(node: dict, depth: int) -> str:
    kind = node.get("type")
    children = node.get("content") or []
    attrs = node.get("attrs") or {}

    if kind == "paragraph":
        return _render_inline(children)
    if kind == "heading":
        level = attrs.get("level") or 1
        level = min(max(int(level), 1), 6) if str(level).isdigit() else 1
        return f"{'#' * level} {_render_inline(children)}"
    if kind in ("bulletList", "orderedList", "taskList"):
        return _render_list(node, depth)
    if kind == "blockquote":
        inner = "\n\n".join(b for b in _render_blocks(children, depth) if b)
        return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
    if kind == "codeBlock":
        return f"```\n{_render_inline(children)}\n```"
    if kind == "horizontalRule":
        return "---"
    if kind == "text":
        return _render_inline([node])
    # Unknown container: keep its text.
    return "\n\n".join(b for b in _render_blocks(children, depth) if b)


def _render_list(node: dict, depth: int) -> str:
    kind = node.get("type")
    start = (node.get("attrs") or {}).get("start") or 1
    indent = "  " * depth
    lines = []
    for i, item in enumerate(node.get("content") or []):
        if not isinstance(item, dict):
            continue
        if kind == "orderedList":
            marker = f"{start + i}."
        elif item.get("type") == "taskItem":
            checked = (item.get("attrs") or {}).get("checked")
            marker = "- [x]" if checked else "- [ ]"
        else:
            marker = "-"
        parts = []
        for child in item.get("content") or []:
            if not isinstance(child, dict):
                continue
            if child.get("type") in ("bulletList", "orderedList", "taskList"):
                parts.append(_render_list(child, depth + 1))
            else:
                parts.append(_render_block(child, depth + 1))
        first, *rest = parts or [""]
        lines.append(f"{indent}{marker} {first}".rstrip())
        lines.extend(p for p in rest if p)
    return "\n".join(lines)


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
            out.append(_render_inline(node.get("content") or []))
            continue
        text = node.get("text") or ""
        href = None
        for mark in node.get("marks") or []:
            mark_type = mark.get("type") if isinstance(mark, dict) else None
            if mark_type == "bold":
                text = f"**{text}**"
            elif mark_type == "italic":
                text = f"*{text}*"
            elif mark_type == "code":
                text = f"`{text}`"
            elif mark_type == "link":
                href = (mark.get("attrs") or {}).get("href")
        out.append(f"[{text}]({href})" if href else text)
    return "".join(out)


class _HTMLToMarkdown(HTMLParser):
    """Minimal HTML -> Markdown for Granola's summary/share-page HTML."""

    _BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        self.list_depth = 0
        self.prefix = ""

    def _flush(self) -> None:
        text = "".join(self.current).strip()
        if text:
            self.lines.append(self.prefix + text)
        self.current = []
        self.prefix = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("ul", "ol"):
            self._flush()
            self.list_depth += 1
        elif tag in self._BLOCKS:
            self._flush()
            if tag.startswith("h") and tag[1:].isdigit():
                self.prefix = "#" * int(tag[1:]) + " "
            elif tag == "li":
                self.prefix = "  " * max(self.list_depth - 1, 0) + "- "
            elif tag == "hr":
                self.lines.append("---")
        elif tag in ("strong", "b"):
            self.current.append("**")
        elif tag in ("em", "i"):
            self.current.append("*")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("ul", "ol"):
            self._flush()
            self.list_depth = max(self.list_depth - 1, 0)
        elif tag in self._BLOCKS:
            self._flush()
        elif tag in ("strong", "b"):
            self.current.append("**")
        elif tag in ("em", "i"):
            self.current.append("*")

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

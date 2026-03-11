"""Allowlist-based HTML sanitizer for contenteditable content."""

from __future__ import annotations

from html.parser import HTMLParser

_ALLOWED_TAGS: frozenset[str] = frozenset({"b", "i", "code", "a", "br"})
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {"a": frozenset({"href"})}


class _SanitizingParser(HTMLParser):
    """Strips all tags/attributes except the allowlisted ones."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            return
        allowed_attrs = _ALLOWED_ATTRS.get(tag, frozenset())
        attr_str = ""
        for name, value in attrs:
            if name in allowed_attrs and value is not None:
                safe_val = value.replace("&", "&amp;").replace('"', "&quot;")
                attr_str += f' {name}="{safe_val}"'
        self.parts.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _ALLOWED_TAGS and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        safe = data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.parts.append(safe)


def sanitize_html(html: str) -> str:
    """Sanitize HTML, keeping only allowed tags and attributes."""
    parser = _SanitizingParser()
    parser.feed(html)
    return "".join(parser.parts)

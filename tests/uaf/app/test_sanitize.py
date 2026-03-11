"""Tests for HTML sanitizer."""

from __future__ import annotations

from uaf.app.frontend.sanitize import sanitize_html


class TestSanitizeHtml:
    def test_plain_text_unchanged(self) -> None:
        assert sanitize_html("Hello world") == "Hello world"

    def test_allowed_tags_kept(self) -> None:
        assert sanitize_html("<b>bold</b>") == "<b>bold</b>"
        assert sanitize_html("<i>italic</i>") == "<i>italic</i>"
        assert sanitize_html("<code>code</code>") == "<code>code</code>"
        assert sanitize_html("<br>") == "<br>"

    def test_allowed_link(self) -> None:
        result = sanitize_html('<a href="https://example.com">link</a>')
        assert 'href="https://example.com"' in result
        assert "<a" in result
        assert "</a>" in result

    def test_disallowed_tags_stripped(self) -> None:
        result = sanitize_html("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "alert" in result  # text content preserved

    def test_disallowed_attrs_stripped(self) -> None:
        result = sanitize_html('<a href="ok" onclick="bad()">link</a>')
        assert 'href="ok"' in result
        assert "onclick" not in result

    def test_nested_tags(self) -> None:
        result = sanitize_html("<b><i>both</i></b>")
        assert result == "<b><i>both</i></b>"

    def test_div_stripped(self) -> None:
        result = sanitize_html("<div>text</div>")
        assert "<div>" not in result
        assert "text" in result

    def test_empty_string(self) -> None:
        assert sanitize_html("") == ""

    def test_special_chars_escaped(self) -> None:
        result = sanitize_html("a < b & c > d")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_img_stripped(self) -> None:
        result = sanitize_html('<img src="x" onerror="alert(1)">')
        assert "<img" not in result

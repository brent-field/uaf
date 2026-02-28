"""Tests for DocLens — document rendering and editing."""

from __future__ import annotations

from uaf.app.lenses import Lens
from uaf.app.lenses.actions import (
    DeleteNode,
    DeleteText,
    FormatText,
    InsertText,
    RenameArtifact,
    ReorderNodes,
)
from uaf.app.lenses.doc_lens import DocLens
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    CodeBlock,
    Heading,
    Image,
    LayoutHint,
    NodeType,
    Paragraph,
    RawNode,
    TextBlock,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB


def _setup() -> tuple[SecureGraphDB, object, DocLens]:
    """Create a SecureGraphDB with SYSTEM session and DocLens."""
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    session = sdb.system_session()
    return sdb, session, DocLens()


def _contains(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


class TestDocLensProtocol:
    def test_is_lens(self) -> None:
        lens = DocLens()
        assert isinstance(lens, Lens)

    def test_lens_type(self) -> None:
        assert DocLens().lens_type == "doc"

    def test_supported_node_types(self) -> None:
        types = DocLens().supported_node_types
        assert NodeType.ARTIFACT in types
        assert NodeType.PARAGRAPH in types
        assert NodeType.HEADING in types
        assert NodeType.CODE_BLOCK in types
        assert NodeType.IMAGE in types


class TestDocLensRender:
    def test_render_empty_artifact(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Empty Doc")
        art_id = sdb.create_node(session, art)

        view = lens.render(sdb, session, art_id)
        assert view.lens_type == "doc"
        assert view.artifact_id == art_id
        assert view.title == "Empty Doc"
        assert view.content_type == "text/html"
        assert view.node_count == 1
        assert "Empty Doc" in view.content
        assert "<article" in view.content

    def test_render_not_found(self) -> None:
        sdb, session, lens = _setup()
        fake_id = NodeId.generate()
        view = lens.render(sdb, session, fake_id)
        assert view.title == "(not found)"
        assert view.node_count == 0

    def test_render_with_paragraphs(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Test")
        art_id = sdb.create_node(session, art)

        p1 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="First")
        p2 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Second")
        p1_id = sdb.create_node(session, p1)
        p2_id = sdb.create_node(session, p2)
        sdb.create_edge(session, _contains(art_id, p1_id))
        sdb.create_edge(session, _contains(art_id, p2_id))

        view = lens.render(sdb, session, art_id)
        assert view.node_count == 3
        assert "First" in view.content
        assert "Second" in view.content
        assert f'data-node-id="{p1_id}"' in view.content

    def test_render_with_headings(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        h = Heading(meta=make_node_metadata(NodeType.HEADING), text="Section", level=2)
        h_id = sdb.create_node(session, h)
        sdb.create_edge(session, _contains(art_id, h_id))

        view = lens.render(sdb, session, art_id)
        assert "<h2" in view.content
        assert "Section" in view.content

    def test_render_with_code_block(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        cb = CodeBlock(
            meta=make_node_metadata(NodeType.CODE_BLOCK), source="print('hi')", language="python",
        )
        cb_id = sdb.create_node(session, cb)
        sdb.create_edge(session, _contains(art_id, cb_id))

        view = lens.render(sdb, session, art_id)
        assert "<pre" in view.content
        assert "<code" in view.content
        assert "language-python" in view.content
        assert "print(&#x27;hi&#x27;)" in view.content

    def test_render_with_text_block(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        tb = TextBlock(
            meta=make_node_metadata(NodeType.TEXT_BLOCK), text="Some text", format="plain",
        )
        tb_id = sdb.create_node(session, tb)
        sdb.create_edge(session, _contains(art_id, tb_id))

        view = lens.render(sdb, session, art_id)
        assert "text-block" in view.content
        assert "Some text" in view.content

    def test_render_with_image(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        img = Image(
            meta=make_node_metadata(NodeType.IMAGE), uri="blob:abc123", alt_text="Chart",
        )
        img_id = sdb.create_node(session, img)
        sdb.create_edge(session, _contains(art_id, img_id))

        view = lens.render(sdb, session, art_id)
        assert "<img" in view.content
        assert 'src="blob:abc123"' in view.content
        assert 'alt="Chart"' in view.content

    def test_render_with_raw_node(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        raw = RawNode(
            meta=make_node_metadata(NodeType.RAW), raw={"foo": "bar"}, original_type="widget",
        )
        raw_id = sdb.create_node(session, raw)
        sdb.create_edge(session, _contains(art_id, raw_id))

        view = lens.render(sdb, session, art_id)
        assert "raw-node" in view.content
        assert "widget" in view.content

    def test_render_html_escaping(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT),
            title="<script>alert(1)</script>",
        )
        art_id = sdb.create_node(session, art)

        view = lens.render(sdb, session, art_id)
        assert "<script>" not in view.content
        assert "&lt;script&gt;" in view.content

    def test_render_paragraph_style(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH), text="Quote", style="quote",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render(sdb, session, art_id)
        assert 'class="quote"' in view.content


class TestDocLensActions:
    def test_insert_paragraph(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        action = InsertText(parent_id=art_id, text="New paragraph", position=0)
        lens.apply_action(sdb, session, art_id, action)

        children = sdb.get_children(session, art_id)
        assert len(children) == 1
        assert isinstance(children[0], Paragraph)
        assert children[0].text == "New paragraph"

    def test_insert_heading(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        action = InsertText(parent_id=art_id, text="Title", position=0, style="heading")
        lens.apply_action(sdb, session, art_id, action)

        children = sdb.get_children(session, art_id)
        assert len(children) == 1
        assert isinstance(children[0], Heading)
        assert children[0].text == "Title"

    def test_insert_code_block(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        action = InsertText(
            parent_id=art_id, text="x = 1", position=0, style="code_block",
        )
        lens.apply_action(sdb, session, art_id, action)

        children = sdb.get_children(session, art_id)
        assert len(children) == 1
        assert isinstance(children[0], CodeBlock)
        assert children[0].source == "x = 1"

    def test_delete_text(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="To delete")
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        action = DeleteText(node_id=p_id)
        lens.apply_action(sdb, session, art_id, action)

        children = sdb.get_children(session, art_id)
        assert len(children) == 0

    def test_delete_node_action(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Delete me")
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        action = DeleteNode(node_id=p_id)
        lens.apply_action(sdb, session, art_id, action)

        assert sdb.get_node(session, p_id) is None

    def test_format_paragraph_to_heading(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Promote me")
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        action = FormatText(node_id=p_id, style="heading", level=2)
        lens.apply_action(sdb, session, art_id, action)

        node = sdb.get_node(session, p_id)
        assert isinstance(node, Heading)
        assert node.text == "Promote me"
        assert node.level == 2

    def test_format_heading_to_paragraph(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        h = Heading(meta=make_node_metadata(NodeType.HEADING), text="Demote me", level=1)
        h_id = sdb.create_node(session, h)
        sdb.create_edge(session, _contains(art_id, h_id))

        action = FormatText(node_id=h_id, style="paragraph")
        lens.apply_action(sdb, session, art_id, action)

        node = sdb.get_node(session, h_id)
        assert isinstance(node, Paragraph)
        assert node.text == "Demote me"

    def test_reorder_nodes(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        p1 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="First")
        p2 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Second")
        p1_id = sdb.create_node(session, p1)
        p2_id = sdb.create_node(session, p2)
        sdb.create_edge(session, _contains(art_id, p1_id))
        sdb.create_edge(session, _contains(art_id, p2_id))

        # Reverse order
        action = ReorderNodes(parent_id=art_id, new_order=(p2_id, p1_id))
        lens.apply_action(sdb, session, art_id, action)

        children = sdb.get_children(session, art_id)
        assert children[0].meta.id == p2_id
        assert children[1].meta.id == p1_id

    def test_rename_artifact(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Old Name")
        art_id = sdb.create_node(session, art)

        action = RenameArtifact(artifact_id=art_id, title="New Name")
        lens.apply_action(sdb, session, art_id, action)

        node = sdb.get_node(session, art_id)
        assert isinstance(node, Artifact)
        assert node.title == "New Name"

    def test_insert_at_position(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        # Insert three paragraphs in specific order
        lens.apply_action(
            sdb, session, art_id,
            InsertText(parent_id=art_id, text="First", position=0),
        )
        lens.apply_action(
            sdb, session, art_id,
            InsertText(parent_id=art_id, text="Third", position=1),
        )
        lens.apply_action(
            sdb, session, art_id,
            InsertText(parent_id=art_id, text="Second", position=1),
        )

        children = sdb.get_children(session, art_id)
        texts = [c.text for c in children]
        assert texts == ["First", "Second", "Third"]

    def test_unsupported_action_raises(self) -> None:
        from uaf.app.lenses.actions import SetCellValue

        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        art_id = sdb.create_node(session, art)

        action = SetCellValue(cell_id=NodeId.generate(), value=42)
        try:
            lens.apply_action(sdb, session, art_id, action)
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "SetCellValue" in str(e)


class TestDocLensLayoutRender:
    def test_render_layout_empty_artifact(self) -> None:
        """Layout render of empty artifact returns a fallback message."""
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Empty")
        art_id = sdb.create_node(session, art)

        view = lens.render_layout(sdb, session, art_id)
        assert view.lens_type == "doc"
        assert view.artifact_id == art_id
        assert view.title == "Empty"
        assert "No layout data" in view.content

    def test_render_layout_not_found(self) -> None:
        """Layout render with nonexistent artifact returns empty."""
        sdb, session, lens = _setup()
        fake_id = NodeId.generate()
        view = lens.render_layout(sdb, session, fake_id)
        assert view.title == "(not found)"
        assert view.node_count == 0

    def test_render_layout_with_positioned_nodes(self) -> None:
        """Nodes with LayoutHint render at their coordinates."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Layout Doc",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0, width=468.0, height=14.0, font_size=12.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Positioned text",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "left: 72.0pt" in view.content
        assert "top: 100.0pt" in view.content
        assert "Positioned text" in view.content
        assert "layout-block" in view.content
        assert "layout-page" in view.content

    def test_render_layout_fallback_no_layout(self) -> None:
        """Nodes without LayoutHint render in flow section."""
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="No Layout")
        art_id = sdb.create_node(session, art)

        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH), text="No position data",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "No position data" in view.content
        assert "layout-flow" in view.content

    def test_render_layout_multipage(self) -> None:
        """Nodes on different pages render in separate page containers."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Multi-page",
        )
        art_id = sdb.create_node(session, art)

        for pg in range(2):
            layout = LayoutHint(page=pg, x=72.0, y=72.0, width=468.0, height=14.0)
            p = Paragraph(
                meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
                text=f"Page {pg} text",
            )
            p_id = sdb.create_node(session, p)
            sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert view.content.count("layout-page") >= 2
        assert "Page 0 text" in view.content
        assert "Page 1 text" in view.content

    def test_render_layout_html_escaping(self) -> None:
        """Layout render escapes HTML to prevent XSS."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="XSS Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(page=0, x=72.0, y=72.0, width=400.0, height=14.0)
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="<script>alert(1)</script>",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "<script>" not in view.content
        assert "&lt;script&gt;" in view.content

    def test_render_layout_font_styles(self) -> None:
        """Font properties from LayoutHint are applied as inline styles."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Font Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=72.0, width=400.0, height=14.0,
            font_family="Helvetica", font_size=14.0, font_weight="bold",
            color="#336699",
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Styled text",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "font-family: Helvetica" in view.content
        assert "font-size: 14.0pt" in view.content
        assert "font-weight: bold" in view.content
        assert "color: #336699" in view.content

    def test_render_layout_header_footer_tagged(self) -> None:
        """Blocks tagged as header/footer get the distinct CSS class."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="HF Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=10.0, width=400.0, height=12.0,
            header_footer=True,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Page Header",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "layout-header-footer" in view.content
        assert "Page Header" in view.content

    def test_layout_node_has_no_explicit_height(self) -> None:
        """Layout blocks must not set explicit height (avoids text clipping)."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="No Height",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0,
            width=468.0, height=50.0, reading_order=0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Should not have height",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        # The layout-page itself has a height, but individual blocks must not.
        assert "width: 468.0pt" in view.content
        # Extract just the block div (not the page container).
        import re

        block_match = re.search(
            r'class="layout-block"[^>]*style="([^"]*)"', view.content,
        )
        assert block_match is not None
        block_style = block_match.group(1)
        assert "height:" not in block_style

    def test_layout_node_has_nowrap(self) -> None:
        """Layout blocks must use white-space: nowrap to prevent CSS re-wrapping.

        Line breaks come from <br> tags that match the PDF's original line
        positions.  The browser must not add its own wraps on top of those.
        """
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Nowrap Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0,
            width=468.0, height=14.0, reading_order=0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Should not be re-wrapped by the browser",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        import re

        block_match = re.search(
            r'class="layout-block"[^>]*style="([^"]*)"', view.content,
        )
        assert block_match is not None
        block_style = block_match.group(1)
        assert "white-space: nowrap" in block_style

    def test_layout_node_has_z_index(self) -> None:
        """Layout blocks include z-index derived from reading_order."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Z-Index Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0,
            width=468.0, height=14.0, reading_order=5,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Z-indexed block",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "z-index: 995" in view.content

    def test_layout_node_preserves_line_breaks(self) -> None:
        """Newlines in node text render as <br> in layout view."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Line Break Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0, width=468.0, height=30.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Line 1\nLine 2",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "Line 1<br>Line 2" in view.content

    def test_layout_node_rotation(self) -> None:
        """Rotated nodes get CSS transform style."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Rotation Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=10.0, y=100.0, width=12.0, height=400.0,
            rotation=-90.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Rotated sidebar",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "transform: rotate(-90.0deg)" in view.content
        assert "transform-origin: top left" in view.content

    def test_render_layout_first_line_bold(self) -> None:
        """Blocks with first_line_weight render first line in a bold span."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Bold Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0, width=400.0, height=40.0,
            font_size=10.0, first_line_weight="bold",
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Author Name\nUniversity of Something",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        # First line should be in a bold span.
        assert '<span style="font-weight: bold">Author Name</span>' in view.content
        # Second line should NOT be in a bold span.
        assert "University of Something" in view.content
        # The rest text should follow a <br> tag.
        assert "<br>University of Something" in view.content

    def test_render_layout_first_line_bold_single_line(self) -> None:
        """Single-line block with first_line_weight still wraps in a span."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Bold Single",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0, width=200.0, height=14.0,
            font_size=10.0, first_line_weight="bold",
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Bold Only",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert '<span style="font-weight: bold">Bold Only</span>' in view.content

    def test_render_layout_no_first_line_weight(self) -> None:
        """Blocks without first_line_weight render plain text with <br>."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Normal Test",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0, width=400.0, height=40.0,
            font_size=10.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Line 1\nLine 2",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "Line 1<br>Line 2" in view.content
        # No <span> wrapping for first-line bold.
        assert '<span style="font-weight:' not in view.content

    def test_render_layout_font_family_with_commas(self) -> None:
        """CSS font stacks with commas are preserved; double-quotes become single."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Font Stack",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=72.0, width=400.0, height=14.0,
            font_family='"Times New Roman", Times, serif',
            font_size=10.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Font stack text",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        # Double-quotes converted to single-quotes to avoid breaking style="...".
        assert "font-family: 'Times New Roman', Times, serif" in view.content
        # font-size must also survive (not truncated by broken quotes).
        assert "font-size: 10.0pt" in view.content

    def test_layout_block_data_attributes(self) -> None:
        """Layout blocks include data-page, data-node-type, data-reading-order, etc."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Data Attrs",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0, width=468.0, height=14.0,
            reading_order=3, rotation=-90.0, first_line_weight="bold",
            font_size=10.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Inspector test",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert 'data-page="0"' in view.content
        assert 'data-node-type="paragraph"' in view.content
        assert 'data-reading-order="3"' in view.content
        assert 'data-height="14.0"' in view.content
        assert 'data-rotation="-90.0"' in view.content
        assert 'data-first-line-weight="bold"' in view.content

    def test_layout_block_data_attributes_heading(self) -> None:
        """Heading nodes report data-node-type='heading'."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Heading Type",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=72.0, width=400.0, height=20.0,
            font_size=18.0,
        )
        h = Heading(
            meta=make_node_metadata(NodeType.HEADING, layout=layout),
            text="Big Title", level=1,
        )
        h_id = sdb.create_node(session, h)
        sdb.create_edge(session, _contains(art_id, h_id))

        view = lens.render_layout(sdb, session, art_id)
        assert 'data-node-type="heading"' in view.content

    def test_layout_block_data_attributes_optional_fields_absent(self) -> None:
        """Data attributes are omitted when the corresponding fields are None."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="Sparse Layout",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=72.0, width=400.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Sparse",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert 'data-node-type="paragraph"' in view.content
        assert 'data-page="0"' in view.content
        # Optional fields should be absent.
        assert "data-rotation" not in view.content
        assert "data-first-line-weight" not in view.content
        assert "data-height" not in view.content
        assert "data-reading-order" not in view.content

    def test_layout_node_no_rotation_for_horizontal(self) -> None:
        """Horizontal text (rotation=None) has no CSS transform."""
        sdb, session, lens = _setup()
        art_layout = LayoutHint(width=612.0, height=792.0)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title="No Rotation",
        )
        art_id = sdb.create_node(session, art)

        layout = LayoutHint(
            page=0, x=72.0, y=100.0, width=468.0, height=14.0,
        )
        p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
            text="Normal text",
        )
        p_id = sdb.create_node(session, p)
        sdb.create_edge(session, _contains(art_id, p_id))

        view = lens.render_layout(sdb, session, art_id)
        assert "transform:" not in view.content

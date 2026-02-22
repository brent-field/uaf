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

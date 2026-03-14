"""Tests for new doc editor routes (update-text, insert-at, reorder, convert, split-block)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.actions import InsertText
from uaf.app.lenses.doc_lens import DocLens
from uaf.core.nodes import (
    Heading,
    Paragraph,
)
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB

if TYPE_CHECKING:
    from uaf.core.node_id import NodeId
    from uaf.security.secure_graph_db import Session


def _setup_app() -> tuple[TestClient, SecureGraphDB, Session, NodeId]:
    """Create app with a doc artifact that has two paragraphs."""
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    session = sdb.system_session()
    registry = LensRegistry()
    registry.register(DocLens())

    app = create_app(sdb, registry)
    client = TestClient(app)

    # Register a user via the API to get a valid JWT cookie
    resp = client.post(
        "/api/auth/register",
        json={"display_name": "TestUser", "password": "testpass123"},
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    # Create artifact via API
    resp = client.post(
        "/api/artifacts",
        json={"title": "Test Doc"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    art_id_str = resp.json()["id"]

    # Parse the art_id for direct DB access
    import uuid as _uuid

    from uaf.core.node_id import NodeId as NId

    art_id: NodeId = NId(value=_uuid.UUID(art_id_str))

    # Add two paragraphs using the lens directly
    lens = DocLens()
    lens.apply_action(
        sdb, session, art_id,
        InsertText(parent_id=art_id, text="First paragraph", position=0),
    )
    lens.apply_action(
        sdb, session, art_id,
        InsertText(parent_id=art_id, text="Second paragraph", position=1),
    )

    # Set cookie on client for frontend routes
    client.cookies.set("uaf_token", token)

    return client, sdb, session, art_id


class TestUpdateText:
    def test_update_text_returns_204(self) -> None:
        client, sdb, session, art_id = _setup_app()
        children = sdb.get_children(session, art_id)
        node_id = str(children[0].meta.id)
        resp = client.post(
            f"/artifacts/{art_id}/action/update-text",
            data={
                "node_id": node_id,
                "text": "Updated text",
                "content_format": "plain",
            },
        )
        assert resp.status_code == 204

    def test_update_text_persists(self) -> None:
        client, sdb, session, art_id = _setup_app()
        children = sdb.get_children(session, art_id)
        node_id = str(children[0].meta.id)
        client.post(
            f"/artifacts/{art_id}/action/update-text",
            data={"node_id": node_id, "text": "New content"},
        )
        updated = sdb.get_node(session, children[0].meta.id)
        assert isinstance(updated, Paragraph)
        assert updated.text == "New content"


class TestInsertAt:
    def test_insert_at_position(self) -> None:
        client, sdb, session, art_id = _setup_app()
        resp = client.post(
            f"/artifacts/{art_id}/action/insert-at",
            data={"position": "1", "style": "paragraph", "text": "Middle"},
        )
        assert resp.status_code == 200
        children = sdb.get_children(session, art_id)
        assert len(children) == 3
        assert isinstance(children[1], Paragraph)
        assert children[1].text == "Middle"


class TestReorder:
    def test_reorder_returns_204(self) -> None:
        client, sdb, session, art_id = _setup_app()
        children = sdb.get_children(session, art_id)
        ids = [str(c.meta.id) for c in reversed(children)]
        resp = client.post(
            f"/artifacts/{art_id}/action/reorder",
            data={"order": ",".join(ids)},
        )
        assert resp.status_code == 204


class TestConvert:
    def test_convert_to_heading(self) -> None:
        client, sdb, session, art_id = _setup_app()
        children = sdb.get_children(session, art_id)
        node_id = str(children[0].meta.id)
        resp = client.post(
            f"/artifacts/{art_id}/action/convert",
            data={"node_id": node_id, "new_style": "heading", "level": "2"},
        )
        assert resp.status_code == 200
        updated = sdb.get_node(session, children[0].meta.id)
        assert isinstance(updated, Heading)
        assert updated.level == 2


class TestSplitBlock:
    def test_split_creates_new_block(self) -> None:
        client, sdb, session, art_id = _setup_app()
        children = sdb.get_children(session, art_id)
        node_id = str(children[0].meta.id)
        resp = client.post(
            f"/artifacts/{art_id}/action/split-block",
            data={
                "node_id": node_id,
                "before_text": "First",
                "after_text": "paragraph",
            },
        )
        assert resp.status_code == 200
        children_after = sdb.get_children(session, art_id)
        assert len(children_after) == 3


class TestRenderNewBlockTypes:
    def test_bullet_list_item(self) -> None:
        from uaf.app.frontend.routes import _render_single_block
        from uaf.core.nodes import BulletListItem, NodeType, make_node_metadata

        node = BulletListItem(
            meta=make_node_metadata(NodeType.BULLET_LIST_ITEM),
            text="hello",
        )
        html, text, node_type = _render_single_block(node)
        assert node_type == "bullet_list_item"
        assert text == "hello"
        assert "hello" in html

    def test_numbered_list_item(self) -> None:
        from uaf.app.frontend.routes import _render_single_block
        from uaf.core.nodes import NodeType, NumberedListItem, make_node_metadata

        node = NumberedListItem(
            meta=make_node_metadata(NodeType.NUMBERED_LIST_ITEM),
            text="step",
        )
        _html, text, node_type = _render_single_block(node)
        assert node_type == "numbered_list_item"
        assert text == "step"

    def test_blockquote(self) -> None:
        from uaf.app.frontend.routes import _render_single_block
        from uaf.core.nodes import Blockquote, NodeType, make_node_metadata

        node = Blockquote(
            meta=make_node_metadata(NodeType.BLOCKQUOTE),
            text="wise words",
        )
        _html, text, node_type = _render_single_block(node)
        assert node_type == "blockquote"
        assert text == "wise words"

    def test_divider(self) -> None:
        from uaf.app.frontend.routes import _render_single_block
        from uaf.core.nodes import Divider, NodeType, make_node_metadata

        node = Divider(meta=make_node_metadata(NodeType.DIVIDER))
        html, _text, node_type = _render_single_block(node)
        assert node_type == "divider"
        assert "<hr>" in html

    def test_bullet_list_html_format(self) -> None:
        from uaf.app.frontend.routes import _render_single_block
        from uaf.core.nodes import BulletListItem, NodeType, make_node_metadata

        node = BulletListItem(
            meta=make_node_metadata(NodeType.BULLET_LIST_ITEM),
            text="<b>bold</b>",
            content_format="html",
        )
        html, _text, _node_type = _render_single_block(node)
        assert "<b>bold</b>" in html  # HTML passed through


class TestSidebar:
    def test_sidebar_returns_grouped_html(self) -> None:
        """GET /sidebar returns HTML with artifact sections."""
        client, _sdb, _session, _art_id = _setup_app()
        resp = client.get("/sidebar")
        assert resp.status_code == 200
        assert "Documents" in resp.text

    def test_sidebar_unauthenticated_returns_empty(self) -> None:
        """GET /sidebar without auth returns empty HTML."""
        db = GraphDB()
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        registry = LensRegistry()
        registry.register(DocLens())
        app = create_app(sdb, registry)
        client = TestClient(app)
        resp = client.get("/sidebar")
        assert resp.status_code == 200
        assert resp.text == ""

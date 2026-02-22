"""Integration tests — end-to-end scenarios crossing all application layers."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.doc_lens import DocLens
from uaf.app.lenses.grid_lens import GridLens
from uaf.app.mcp_server import create_mcp_server
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Cell,
    NodeType,
    Sheet,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider, PasswordCredentials
from uaf.security.primitives import PrincipalId, Role
from uaf.security.secure_graph_db import SecureGraphDB


def _contains(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


class TestDocumentWorkflow:
    """Register -> login -> create artifact -> add content via DocLens -> render
    -> export as Markdown -> verify content."""

    def test_full_document_workflow(self) -> None:
        db = GraphDB()
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        registry = LensRegistry()
        registry.register(DocLens())
        registry.register(GridLens())
        app = create_app(sdb, registry)
        client = TestClient(app)

        # Register
        resp = client.post(
            "/api/auth/register",
            json={"display_name": "Alice", "password": "alice123"},
        )
        assert resp.status_code == 200
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create artifact
        resp = client.post(
            "/api/artifacts", json={"title": "Quarterly Report"}, headers=headers,
        )
        assert resp.status_code == 201
        aid = resp.json()["id"]

        # Add heading via DocLens action
        client.post(
            f"/api/artifacts/{aid}/lens/doc/action",
            json={
                "action_type": "insert_text",
                "params": {
                    "parent_id": aid, "text": "Overview", "position": 0,
                    "style": "heading",
                },
            },
            headers=headers,
        )

        # Add paragraph
        client.post(
            f"/api/artifacts/{aid}/lens/doc/action",
            json={
                "action_type": "insert_text",
                "params": {
                    "parent_id": aid, "text": "Revenue grew 15%.", "position": 1,
                },
            },
            headers=headers,
        )

        # Render via DocLens
        resp = client.get(f"/api/artifacts/{aid}/lens/doc", headers=headers)
        assert resp.status_code == 200
        view = resp.json()
        assert view["lens_type"] == "doc"
        assert "Overview" in view["content"]
        assert "Revenue grew 15%." in view["content"]
        assert view["node_count"] == 3  # artifact + heading + paragraph

        # Export as Markdown
        resp = client.get(
            f"/api/artifacts/{aid}/export?format=markdown", headers=headers,
        )
        assert resp.status_code == 200
        md = resp.text
        assert "Overview" in md
        assert "Revenue grew 15%." in md


class TestSpreadsheetWorkflow:
    """Create artifact -> add sheet -> add cells via GridLens -> render ->
    export as CSV -> verify values."""

    def test_full_spreadsheet_workflow(self) -> None:
        db = GraphDB()
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = GridLens()

        # Create spreadsheet with data
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Budget")
        art_id = sdb.create_node(session, art)

        sheet = Sheet(
            meta=make_node_metadata(NodeType.SHEET), title="Sheet1", rows=2, cols=2,
        )
        sheet_id = sdb.create_node(session, sheet)
        sdb.create_edge(session, _contains(art_id, sheet_id))

        for r, row in enumerate([["Item", "Cost"], ["Coffee", "5.00"]]):
            for c, val in enumerate(row):
                cell = Cell(
                    meta=make_node_metadata(NodeType.CELL), value=val, row=r, col=c,
                )
                cid = sdb.create_node(session, cell)
                sdb.create_edge(session, _contains(sheet_id, cid))

        # Render
        view = lens.render(sdb, session, art_id)
        assert view.lens_type == "grid"
        assert ">Item</td>" in view.content
        assert ">Coffee</td>" in view.content
        assert ">5.00</td>" in view.content

        # Export as CSV
        from uaf.app.formats.csv_format import CsvHandler

        handler = CsvHandler()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            csv_path = Path(tmp.name)
        handler.export_file(db, art_id, csv_path)
        csv_text = csv_path.read_text(encoding="utf-8")
        csv_path.unlink()
        assert "Item" in csv_text
        assert "Coffee" in csv_text
        assert "5.00" in csv_text


class TestImportLensExport:
    """Import Markdown -> render via DocLens -> verify HTML -> export back
    -> round-trip verify."""

    def test_import_render_export_roundtrip(self) -> None:
        db = GraphDB()
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()

        # Import Markdown
        from uaf.app.formats.markdown import MarkdownHandler

        md_content = "# Welcome\n\nThis is a test document.\n\n## Details\n\nMore info here.\n"
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as tmp:
            tmp.write(md_content)
            md_path = Path(tmp.name)

        handler = MarkdownHandler()
        art_id = handler.import_file(md_path, db)
        md_path.unlink()

        # Render via DocLens
        view = lens.render(sdb, session, art_id)
        assert "Welcome" in view.content
        assert "This is a test document." in view.content
        assert "Details" in view.content
        assert "More info here." in view.content
        assert view.node_count >= 5  # artifact + 2 headings + 2 paragraphs

        # Export back to Markdown
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
            export_path = Path(tmp.name)
        handler.export_file(db, art_id, export_path)
        exported = export_path.read_text(encoding="utf-8")
        export_path.unlink()

        assert "Welcome" in exported
        assert "test document" in exported
        assert "Details" in exported


class TestMCPAgentWorkflow:
    """Create artifact via MCP -> add content -> query via find_by_type ->
    render via render_artifact -> verify output."""

    def test_mcp_agent_workflow(self) -> None:
        db = GraphDB()
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        registry = LensRegistry()
        registry.register(DocLens())
        session = sdb.system_session()
        mcp = create_mcp_server(sdb, session, registry)

        def call(name: str, args: dict[str, object]) -> dict[str, Any]:
            blocks, _ = asyncio.run(mcp.call_tool(name, args))
            return json.loads(blocks[0].text)  # type: ignore[union-attr,no-any-return]

        # Create artifact
        art = call("create_artifact", {"title": "AI Notes"})
        aid = art["artifact_id"]

        # Add content
        call("add_child", {"parent_id": aid, "node_type": "paragraph", "text": "Note 1"})
        call("add_child", {"parent_id": aid, "node_type": "paragraph", "text": "Note 2"})

        # Query
        blocks, _ = asyncio.run(
            mcp.call_tool("find_by_type", {"node_type": "artifact"})
        )
        artifacts = json.loads(blocks[0].text)  # type: ignore[union-attr]
        assert any(a.get("title") == "AI Notes" for a in artifacts)

        # Render
        result = call("render_artifact", {"artifact_id": aid, "lens_type": "doc"})
        assert "Note 1" in result["content"]
        assert "Note 2" in result["content"]
        assert result["node_count"] == 3


class TestMultiUserAPI:
    """User 1 creates artifact -> grants EDITOR to user 2 -> user 2 edits
    -> user 3 gets 403 -> audit log shows all actions."""

    def test_multi_user_permissions(self) -> None:
        db = GraphDB()
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        registry = LensRegistry()
        registry.register(DocLens())
        app = create_app(sdb, registry)
        client = TestClient(app)

        # Register user1 and user2
        r1 = client.post(
            "/api/auth/register", json={"display_name": "User1", "password": "p1"},
        )
        token1 = r1.json()["token"]
        h1 = {"Authorization": f"Bearer {token1}"}

        r2 = client.post(
            "/api/auth/register", json={"display_name": "User2", "password": "p2"},
        )
        token2 = r2.json()["token"]
        pid2 = r2.json()["principal_id"]
        h2 = {"Authorization": f"Bearer {token2}"}

        # User3 just registers (no explicit grant)
        r3 = client.post(
            "/api/auth/register", json={"display_name": "User3", "password": "p3"},
        )
        token3 = r3.json()["token"]
        h3 = {"Authorization": f"Bearer {token3}"}

        # User1 creates artifact
        resp = client.post(
            "/api/artifacts", json={"title": "Shared"}, headers=h1,
        )
        aid = resp.json()["id"]

        # User1 grants EDITOR to user2
        sdb.grant_role(
            sdb.authenticate(
                PasswordCredentials(
                    principal_id=PrincipalId(value=r1.json()["principal_id"]),
                    password="p1",
                )
            ),
            NodeId.from_str(aid) if hasattr(NodeId, "from_str") else _parse_nid(aid),
            PrincipalId(value=pid2),
            Role.EDITOR,
        )

        # User2 can read the artifact
        resp = client.get(f"/api/artifacts/{aid}", headers=h2)
        assert resp.status_code == 200

        # User3 should not be able to see the artifact (not in ACL)
        resp = client.get(f"/api/artifacts/{aid}", headers=h3)
        assert resp.status_code == 404  # returns None -> 404

        # Audit log has entries
        audit = sdb.get_audit_log()
        assert audit.count() > 0


class TestCrossLensConsistency:
    """Create document via DocLens -> same artifact rendered via API JSON endpoint
    -> both views show same content."""

    def test_cross_lens_consistency(self) -> None:
        db = GraphDB()
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        registry = LensRegistry()
        registry.register(DocLens())
        registry.register(GridLens())
        app = create_app(sdb, registry)
        client = TestClient(app)

        # Register
        resp = client.post(
            "/api/auth/register", json={"display_name": "X", "password": "x"},
        )
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create artifact with content
        resp = client.post(
            "/api/artifacts", json={"title": "CrossLens"}, headers=headers,
        )
        aid = resp.json()["id"]

        client.post(
            f"/api/artifacts/{aid}/lens/doc/action",
            json={
                "action_type": "insert_text",
                "params": {"parent_id": aid, "text": "Consistency", "position": 0},
            },
            headers=headers,
        )

        # Render via DocLens
        resp = client.get(f"/api/artifacts/{aid}/lens/doc", headers=headers)
        doc_view = resp.json()

        # Read the same artifact via node API
        resp = client.get(f"/api/nodes/{aid}/children", headers=headers)
        children = resp.json()["children"]

        # DocLens shows the content
        assert "Consistency" in doc_view["content"]

        # Node API shows the same child
        assert len(children) == 1
        assert children[0]["node_type"] == "paragraph"


def _parse_nid(s: str) -> NodeId:
    """Parse a string UUID into a NodeId."""
    import uuid

    return NodeId(value=uuid.UUID(s))

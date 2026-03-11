"""Tests for grid editor routes with positional insert/delete."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.grid_lens import GridLens
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, utc_now
from uaf.core.nodes import (
    Cell,
    NodeType,
    Sheet,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB

if TYPE_CHECKING:
    from uaf.core.node_id import NodeId
    from uaf.security.secure_graph_db import Session


def _setup_grid_app() -> tuple[TestClient, SecureGraphDB, Session, NodeId]:
    """Create app with a 3x3 spreadsheet."""
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    session = sdb.system_session()
    registry = LensRegistry()
    registry.register(GridLens())

    app = create_app(sdb, registry)
    client = TestClient(app)

    # Register a user via the API to get a valid JWT cookie
    resp = client.post(
        "/api/auth/register",
        json={"display_name": "GridUser", "password": "testpass123"},
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    # Create artifact via API
    resp = client.post(
        "/api/artifacts",
        json={"title": "Test Sheet"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201

    import uuid as _uuid

    from uaf.core.node_id import NodeId as NId

    art_id: NodeId = NId(value=_uuid.UUID(resp.json()["id"]))

    # Build the sheet structure directly in DB
    sheet = Sheet(
        meta=make_node_metadata(NodeType.SHEET), title="Sheet1", rows=3, cols=3,
    )
    sheet_id = sdb.create_node(session, sheet)
    sdb.create_edge(
        session,
        Edge(
            id=EdgeId.generate(), source=art_id, target=sheet_id,
            edge_type=EdgeType.CONTAINS, created_at=utc_now(),
        ),
    )

    for r in range(3):
        for c in range(3):
            cell = Cell(
                meta=make_node_metadata(NodeType.CELL),
                value=f"R{r}C{c}", row=r, col=c,
            )
            cid = sdb.create_node(session, cell)
            sdb.create_edge(
                session,
                Edge(
                    id=EdgeId.generate(), source=sheet_id, target=cid,
                    edge_type=EdgeType.CONTAINS, created_at=utc_now(),
                ),
            )

    # Set cookie on client for frontend routes
    client.cookies.set("uaf_token", token)

    return client, sdb, session, art_id


class TestGridWithPosition:
    def test_add_row_default_appends(self) -> None:
        client, sdb, session, art_id = _setup_grid_app()
        resp = client.post(f"/artifacts/{art_id}/grid/add-row")
        assert resp.status_code == 200
        # Should have 4 rows now
        children = sdb.get_children(session, art_id)
        sheet = next(c for c in children if isinstance(c, Sheet))
        assert sheet.rows == 4

    def test_grid_table_has_column_headers(self) -> None:
        client, _sdb, _session, art_id = _setup_grid_app()
        resp = client.get(f"/artifacts/{art_id}/grid")
        assert resp.status_code == 200
        assert "<th>A</th>" in resp.text
        assert "<th>B</th>" in resp.text
        assert "<th>C</th>" in resp.text

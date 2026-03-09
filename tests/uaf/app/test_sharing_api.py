"""Tests for the sharing/bundle API endpoints."""

from __future__ import annotations

import zipfile

from fastapi.testclient import TestClient

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.doc_lens import DocLens
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Heading,
    NodeType,
    Paragraph,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider, PasswordCredentials
from uaf.security.secure_graph_db import SecureGraphDB


def _setup() -> tuple[TestClient, SecureGraphDB, str]:
    """Create app, register a user, return (client, db, token)."""
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)

    registry = LensRegistry()
    registry.register(DocLens())

    app = create_app(sdb, registry)
    client = TestClient(app)

    principal = auth.create_principal("TestUser", "secret123")
    session = sdb.authenticate(
        PasswordCredentials(principal_id=principal.id, password="secret123"),
    )

    return client, sdb, session.token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_artifact(sdb: SecureGraphDB, token: str, title: str = "Test Doc") -> NodeId:
    """Create an artifact with children, properly registered in security layer.

    Uses the import endpoint pattern: create via raw db, then register in security layer.
    """
    from uaf.security.auth import TokenCredentials

    session = sdb.authenticate(TokenCredentials(token=token))
    db = sdb._db

    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)
    art_id = db.create_node(art)

    h = Heading(meta=make_node_metadata(NodeType.HEADING), text="Hello", level=1)
    h_id = db.create_node(h)
    db.create_edge(Edge(
        id=EdgeId.generate(), source=art_id, target=h_id,
        edge_type=EdgeType.CONTAINS, created_at=utc_now(),
    ))

    p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="World.")
    p_id = db.create_node(p)
    db.create_edge(Edge(
        id=EdgeId.generate(), source=art_id, target=p_id,
        edge_type=EdgeType.CONTAINS, created_at=utc_now(),
    ))

    # Register in security layer
    from uaf.security.acl import ACL, ACLEntry
    from uaf.security.primitives import Role

    resolver = sdb._resolver
    resolver.register_artifact(art_id)
    acl = ACL(
        artifact_id=art_id,
        entries=(
            ACLEntry(
                principal_id=session.principal.id,
                role=Role.OWNER,
                granted_at=utc_now(),
                granted_by=session.principal.id,
            ),
        ),
    )
    resolver.set_acl(acl)
    resolver.register_parent(h_id, art_id)
    resolver.register_parent(p_id, art_id)

    return art_id


# ---------------------------------------------------------------------------
# TestExportBundleEndpoint
# ---------------------------------------------------------------------------


class TestExportBundleEndpoint:
    """Verify the bundle export API endpoint."""

    def test_export_returns_uaf_zip(self) -> None:
        client, sdb, token = _setup()
        art_id = _make_artifact(sdb, token)

        resp = client.get(
            f"/api/sharing/artifacts/{art_id}/bundle",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

        # Verify it's a valid zip with manifest
        import io

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert "manifest.json" in zf.namelist()

    def test_export_404_for_missing_artifact(self) -> None:
        client, _, token = _setup()
        fake_id = NodeId.generate()

        resp = client.get(
            f"/api/sharing/artifacts/{fake_id}/bundle",
            headers=_auth(token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestImportBundleEndpoint
# ---------------------------------------------------------------------------


class TestImportBundleEndpoint:
    """Verify the bundle import API endpoint."""

    def test_import_creates_artifact(self) -> None:
        client, sdb, token = _setup()
        art_id = _make_artifact(sdb, token, title="Bundle Doc")

        # Export
        resp = client.get(
            f"/api/sharing/artifacts/{art_id}/bundle",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        bundle_data = resp.content

        # Import into same system (new artifact IDs via snapshot)
        resp = client.post(
            "/api/sharing/artifacts/import-bundle",
            headers=_auth(token),
            files={"file": ("test.uaf", bundle_data, "application/zip")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["imported_ids"]) >= 1

    def test_import_registers_acl(self) -> None:
        client, sdb, token = _setup()
        art_id = _make_artifact(sdb, token, title="ACL Test")

        # Export
        resp = client.get(
            f"/api/sharing/artifacts/{art_id}/bundle",
            headers=_auth(token),
        )
        bundle_data = resp.content

        # Import
        resp = client.post(
            "/api/sharing/artifacts/import-bundle",
            headers=_auth(token),
            files={"file": ("test.uaf", bundle_data, "application/zip")},
        )
        assert resp.status_code == 201
        data = resp.json()

        # Verify the imported artifact is accessible via the security layer
        import uuid

        from uaf.security.auth import TokenCredentials

        imported_id = NodeId(value=uuid.UUID(data["imported_ids"][0]))
        session = sdb.authenticate(TokenCredentials(token=token))
        art = sdb.get_node(session, imported_id)
        assert art is not None

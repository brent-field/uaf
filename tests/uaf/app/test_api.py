"""Tests for the REST API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.doc_lens import DocLens
from uaf.app.lenses.grid_lens import GridLens
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
    registry.register(GridLens())

    app = create_app(sdb, registry)
    client = TestClient(app)

    # Register a test user via the auth provider directly
    principal = auth.create_principal("TestUser", "secret123")
    session = sdb.authenticate(
        PasswordCredentials(principal_id=principal.id, password="secret123")
    )
    token = session.token

    return client, sdb, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestAuth:
    def test_login(self) -> None:
        client, _, token = _setup()
        # Get principal_id from the token we already have
        resp = client.get("/api/auth/me", headers=_auth(token))
        pid = resp.json()["principal_id"]

        resp = client.post(
            "/api/auth/login",
            json={"principal_id": pid, "password": "secret123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["display_name"] == "TestUser"

    def test_login_bad_password(self) -> None:
        client, _, token = _setup()
        resp = client.get("/api/auth/me", headers=_auth(token))
        pid = resp.json()["principal_id"]

        resp = client.post(
            "/api/auth/login",
            json={"principal_id": pid, "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_register(self) -> None:
        client, _, _ = _setup()
        resp = client.post(
            "/api/auth/register",
            json={"display_name": "NewUser", "password": "pass456"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "NewUser"
        assert "token" in data

    def test_me(self) -> None:
        client, _, token = _setup()
        resp = client.get("/api/auth/me", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "TestUser"

    def test_no_auth_returns_401(self) -> None:
        client, _, _ = _setup()
        resp = client.get("/api/artifacts")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Artifact CRUD tests
# ---------------------------------------------------------------------------


class TestArtifacts:
    def test_create_artifact(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts",
            json={"title": "My Doc"},
            headers=_auth(token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "My Doc"
        assert "id" in data

    def test_list_artifacts(self) -> None:
        client, _, token = _setup()
        # Create two
        client.post("/api/artifacts", json={"title": "A"}, headers=_auth(token))
        client.post("/api/artifacts", json={"title": "B"}, headers=_auth(token))

        resp = client.get("/api/artifacts", headers=_auth(token))
        assert resp.status_code == 200
        arts = resp.json()["artifacts"]
        assert len(arts) == 2

    def test_get_artifact(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts", json={"title": "Test"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        resp = client.get(f"/api/artifacts/{aid}", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test"

    def test_get_artifact_not_found(self) -> None:
        client, _, token = _setup()
        from uaf.core.node_id import NodeId

        fake = str(NodeId.generate())
        resp = client.get(f"/api/artifacts/{fake}", headers=_auth(token))
        assert resp.status_code == 404

    def test_delete_artifact(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts", json={"title": "Del"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        resp = client.delete(f"/api/artifacts/{aid}", headers=_auth(token))
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Node endpoints tests
# ---------------------------------------------------------------------------


class TestNodes:
    def _create_doc_with_paragraph(
        self, client: TestClient, token: str
    ) -> tuple[str, str]:
        """Create artifact + paragraph. Returns (artifact_id, para_node_id)."""
        resp = client.post(
            "/api/artifacts", json={"title": "D"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        # Add paragraph via lens action
        client.post(
            f"/api/artifacts/{aid}/lens/doc/action",
            json={
                "action_type": "insert_text",
                "params": {"parent_id": aid, "text": "Hello", "position": 0},
            },
            headers=_auth(token),
        )

        resp = client.get(f"/api/nodes/{aid}/children", headers=_auth(token))
        children = resp.json()["children"]
        para_id = children[0]["id"]
        return aid, para_id

    def test_get_node(self) -> None:
        client, _, token = _setup()
        _, para_id = self._create_doc_with_paragraph(client, token)
        resp = client.get(f"/api/nodes/{para_id}", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["node_type"] == "paragraph"

    def test_get_children(self) -> None:
        client, _, token = _setup()
        aid, _ = self._create_doc_with_paragraph(client, token)
        resp = client.get(f"/api/nodes/{aid}/children", headers=_auth(token))
        assert resp.status_code == 200
        assert len(resp.json()["children"]) == 1

    def test_delete_node(self) -> None:
        client, _, token = _setup()
        _, para_id = self._create_doc_with_paragraph(client, token)
        resp = client.delete(f"/api/nodes/{para_id}", headers=_auth(token))
        assert resp.status_code == 204

    def test_get_history(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts", json={"title": "H"}, headers=_auth(token),
        )
        aid = resp.json()["id"]
        resp = client.get(f"/api/nodes/{aid}/history", headers=_auth(token))
        assert resp.status_code == 200
        assert len(resp.json()["entries"]) >= 1


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_by_type(self) -> None:
        client, _, token = _setup()
        client.post(
            "/api/artifacts", json={"title": "S"}, headers=_auth(token),
        )
        resp = client.get(
            "/api/search?type=artifact", headers=_auth(token),
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) >= 1

    def test_search_bad_type(self) -> None:
        client, _, token = _setup()
        resp = client.get("/api/search?type=bogus", headers=_auth(token))
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Lens endpoints tests
# ---------------------------------------------------------------------------


class TestLensEndpoints:
    def test_render_doc_lens(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts", json={"title": "Render"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        resp = client.get(
            f"/api/artifacts/{aid}/lens/doc", headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lens_type"] == "doc"
        assert "Render" in data["content"]

    def test_render_unknown_lens(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts", json={"title": "X"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        resp = client.get(
            f"/api/artifacts/{aid}/lens/unknown", headers=_auth(token),
        )
        assert resp.status_code == 404

    def test_apply_lens_action(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts", json={"title": "Act"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        resp = client.post(
            f"/api/artifacts/{aid}/lens/doc/action",
            json={
                "action_type": "insert_text",
                "params": {"parent_id": aid, "text": "New para", "position": 0},
            },
            headers=_auth(token),
        )
        assert resp.status_code == 204

        # Verify content was added
        resp = client.get(
            f"/api/artifacts/{aid}/lens/doc", headers=_auth(token),
        )
        assert "New para" in resp.json()["content"]

    def test_unknown_action_type(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts", json={"title": "X"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        resp = client.post(
            f"/api/artifacts/{aid}/lens/doc/action",
            json={"action_type": "bogus", "params": {}},
            headers=_auth(token),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Import/Export tests
# ---------------------------------------------------------------------------


class TestImportExport:
    def test_import_markdown(self) -> None:
        client, _, token = _setup()

        content = b"# Title\n\nHello world\n"
        resp = client.post(
            "/api/artifacts/import?format=markdown",
            files={"file": ("test.md", content, "text/markdown")},
            headers=_auth(token),
        )
        assert resp.status_code == 201
        assert resp.json()["title"] == "test"

    def test_export_markdown(self) -> None:
        client, _, token = _setup()

        # Create artifact with content
        resp = client.post(
            "/api/artifacts", json={"title": "Exp"}, headers=_auth(token),
        )
        aid = resp.json()["id"]

        # Add a paragraph
        client.post(
            f"/api/artifacts/{aid}/lens/doc/action",
            json={
                "action_type": "insert_text",
                "params": {"parent_id": aid, "text": "Export me", "position": 0},
            },
            headers=_auth(token),
        )

        resp = client.get(
            f"/api/artifacts/{aid}/export?format=markdown",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert "Export me" in resp.text

    def test_import_unknown_format(self) -> None:
        client, _, token = _setup()
        resp = client.post(
            "/api/artifacts/import?format=rtf",
            files={"file": ("test.rtf", b"data", "application/octet-stream")},
            headers=_auth(token),
        )
        assert resp.status_code == 400

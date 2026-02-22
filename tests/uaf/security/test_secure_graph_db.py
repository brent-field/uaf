"""Tests for SecureGraphDB — the security-enforcing wrapper around GraphDB."""

from __future__ import annotations

import pytest

from uaf.core.edges import Edge, EdgeType
from uaf.core.errors import PermissionDeniedError
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import Artifact, NodeType, Paragraph, make_node_metadata
from uaf.db.graph_db import GraphDB
from uaf.security.audit import AuditOutcome
from uaf.security.auth import LocalAuthProvider, PasswordCredentials
from uaf.security.primitives import ANONYMOUS, SYSTEM, Role
from uaf.security.secure_graph_db import SecureGraphDB, Session


def _make_sdb() -> tuple[SecureGraphDB, LocalAuthProvider]:
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    return sdb, auth


def _make_artifact(title: str = "Doc") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _make_paragraph(text: str = "Hello") -> Paragraph:
    return Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)


def _contains_edge(parent: NodeId, child: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=parent,
        target=child,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


def _auth_session(
    sdb: SecureGraphDB, auth: LocalAuthProvider, name: str, password: str = "pass"
) -> Session:
    p = auth.create_principal(name, password)
    return sdb.authenticate(
        PasswordCredentials(principal_id=p.id, password=password)
    )


class TestSystemSession:
    def test_system_session_is_system(self) -> None:
        sdb, _ = _make_sdb()
        session = sdb.system_session()
        assert session.principal is SYSTEM

    def test_system_can_create_anything(self) -> None:
        sdb, _ = _make_sdb()
        session = sdb.system_session()
        art = _make_artifact()
        node_id = sdb.create_node(session, art)
        assert node_id == art.meta.id


class TestAuthentication:
    def test_authenticate_returns_session(self) -> None:
        sdb, auth = _make_sdb()
        p = auth.create_principal("Alice", "pass123")
        creds = PasswordCredentials(principal_id=p.id, password="pass123")
        session = sdb.authenticate(creds)
        assert session.principal.id == p.id
        assert session.token != ""


class TestCreateArtifactAutoACL:
    def test_creator_becomes_owner(self) -> None:
        sdb, auth = _make_sdb()
        p = auth.create_principal("Alice", "pass123")
        session = sdb.authenticate(
            PasswordCredentials(principal_id=p.id, password="pass123")
        )
        art = _make_artifact()
        sdb.create_node(session, art)
        acl = sdb.get_acl(session, art.meta.id)
        assert acl is not None
        assert len(acl.entries) == 1
        assert acl.entries[0].principal_id == p.id
        assert acl.entries[0].role == Role.OWNER


class TestCRUDWithPermissions:
    def test_owner_can_crud(self) -> None:
        sdb, auth = _make_sdb()
        session = _auth_session(sdb, auth, "Alice", "pass123")

        # Create artifact
        art = _make_artifact()
        sdb.create_node(session, art)

        # Create child paragraph
        para = _make_paragraph("Hello")
        sdb.create_node(session, para)
        sdb.create_edge(
            session, _contains_edge(art.meta.id, para.meta.id)
        )

        # Read
        result = sdb.get_node(session, para.meta.id)
        assert result is not None
        assert result.text == "Hello"

        # Update
        updated = Paragraph(meta=para.meta, text="Updated")
        sdb.update_node(session, updated)
        result = sdb.get_node(session, para.meta.id)
        assert result is not None
        assert result.text == "Updated"

        # Delete
        sdb.delete_node(session, para.meta.id)
        result = sdb.get_node(session, para.meta.id)
        assert result is None

    def test_viewer_can_read_not_write(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        viewer = auth.create_principal("Viewer", "pass")
        viewer_session = sdb.authenticate(
            PasswordCredentials(principal_id=viewer.id, password="pass")
        )

        art = _make_artifact()
        sdb.create_node(owner_session, art)
        sdb.grant_role(
            owner_session, art.meta.id, viewer.id, Role.VIEWER
        )

        # Viewer can read
        result = sdb.get_node(viewer_session, art.meta.id)
        assert result is not None

        # Viewer cannot write
        para = _make_paragraph()
        with pytest.raises(PermissionDeniedError):
            sdb.create_node(viewer_session, para)
            sdb.create_edge(
                viewer_session,
                _contains_edge(art.meta.id, para.meta.id),
            )

    def test_editor_can_read_and_write_not_delete(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        editor = auth.create_principal("Editor", "pass")
        editor_session = sdb.authenticate(
            PasswordCredentials(principal_id=editor.id, password="pass")
        )

        art = _make_artifact()
        sdb.create_node(owner_session, art)
        sdb.grant_role(
            owner_session, art.meta.id, editor.id, Role.EDITOR
        )

        # Editor can create child
        para = _make_paragraph()
        sdb.create_node(editor_session, para)
        sdb.create_edge(
            editor_session,
            _contains_edge(art.meta.id, para.meta.id),
        )

        # Editor can read
        assert sdb.get_node(editor_session, para.meta.id) is not None

        # Editor cannot delete
        with pytest.raises(PermissionDeniedError):
            sdb.delete_node(editor_session, para.meta.id)


class TestDeniedAccess:
    def test_no_access_returns_none_on_read(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        stranger_session = _auth_session(sdb, auth, "Stranger")

        art = _make_artifact()
        sdb.create_node(owner_session, art)

        # Stranger gets None (not an error)
        assert sdb.get_node(stranger_session, art.meta.id) is None

    def test_denied_write_raises_error(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        stranger_session = _auth_session(sdb, auth, "Stranger")

        art = _make_artifact()
        sdb.create_node(owner_session, art)

        updated = Artifact(meta=art.meta, title="Hacked")
        with pytest.raises(PermissionDeniedError):
            sdb.update_node(stranger_session, updated)


class TestQueryFiltering:
    def test_find_by_type_filtered(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        stranger_session = _auth_session(sdb, auth, "Stranger")

        art1 = _make_artifact("Doc1")
        art2 = _make_artifact("Doc2")
        sdb.create_node(owner_session, art1)
        sdb.create_node(owner_session, art2)

        # Owner sees both
        results = sdb.find_by_type(owner_session, NodeType.ARTIFACT)
        assert len(results) == 2

        # Stranger sees neither
        results = sdb.find_by_type(stranger_session, NodeType.ARTIFACT)
        assert len(results) == 0

    def test_get_children_filtered(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")

        art = _make_artifact()
        p1 = _make_paragraph("P1")
        p2 = _make_paragraph("P2")
        sdb.create_node(owner_session, art)
        sdb.create_node(owner_session, p1)
        sdb.create_node(owner_session, p2)
        sdb.create_edge(
            owner_session, _contains_edge(art.meta.id, p1.meta.id)
        )
        sdb.create_edge(
            owner_session, _contains_edge(art.meta.id, p2.meta.id)
        )

        children = sdb.get_children(owner_session, art.meta.id)
        assert len(children) == 2


class TestPermissionGrantRevoke:
    def test_grant_and_revoke(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        user2 = auth.create_principal("User2", "pass")
        user2_session = sdb.authenticate(
            PasswordCredentials(principal_id=user2.id, password="pass")
        )

        art = _make_artifact()
        sdb.create_node(owner_session, art)

        # User2 has no access
        assert sdb.get_node(user2_session, art.meta.id) is None

        # Grant EDITOR
        sdb.grant_role(
            owner_session, art.meta.id, user2.id, Role.EDITOR
        )
        assert sdb.get_node(user2_session, art.meta.id) is not None

        # Revoke
        sdb.revoke_role(owner_session, art.meta.id, user2.id)
        assert sdb.get_node(user2_session, art.meta.id) is None

    def test_non_owner_cannot_grant(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        editor = auth.create_principal("Editor", "pass")
        user3 = auth.create_principal("User3", "pass")
        editor_session = sdb.authenticate(
            PasswordCredentials(
                principal_id=editor.id, password="pass"
            )
        )

        art = _make_artifact()
        sdb.create_node(owner_session, art)
        sdb.grant_role(
            owner_session, art.meta.id, editor.id, Role.EDITOR
        )

        # Editor cannot grant
        with pytest.raises(PermissionDeniedError):
            sdb.grant_role(
                editor_session, art.meta.id, user3.id, Role.VIEWER
            )


class TestAuditLogPopulation:
    def test_operations_are_logged(self) -> None:
        sdb, auth = _make_sdb()
        session = _auth_session(sdb, auth, "Owner")

        art = _make_artifact()
        sdb.create_node(session, art)
        para = _make_paragraph()
        sdb.create_node(session, para)
        sdb.create_edge(
            session, _contains_edge(art.meta.id, para.meta.id)
        )

        audit = sdb.get_audit_log()
        assert audit.count() >= 3

    def test_denied_actions_logged(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        stranger_session = _auth_session(sdb, auth, "Stranger")

        art = _make_artifact()
        sdb.create_node(owner_session, art)

        updated = Artifact(meta=art.meta, title="Hacked")
        with pytest.raises(PermissionDeniedError):
            sdb.update_node(stranger_session, updated)

        audit = sdb.get_audit_log()
        denied = audit.denied()
        assert len(denied) >= 1
        assert denied[0].outcome == AuditOutcome.DENIED


class TestPublicRead:
    def test_public_artifact_readable_by_anonymous(self) -> None:
        sdb, auth = _make_sdb()
        owner_session = _auth_session(sdb, auth, "Owner")
        anon_session = Session(principal=ANONYMOUS, token="")

        art = _make_artifact("Public Doc")
        sdb.create_node(owner_session, art)
        sdb.set_public_read(
            owner_session, art.meta.id, public=True
        )

        # Anonymous can read
        assert sdb.get_node(anon_session, art.meta.id) is not None

        # Anonymous cannot write
        updated = Artifact(meta=art.meta, title="Defaced")
        with pytest.raises(PermissionDeniedError):
            sdb.update_node(anon_session, updated)

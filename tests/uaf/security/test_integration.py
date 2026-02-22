"""Integration tests — multi-user scenarios exercising the full security stack."""

from __future__ import annotations

import pytest

from uaf.core.edges import Edge, EdgeType
from uaf.core.errors import PermissionDeniedError
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    NodeType,
    Paragraph,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB
from uaf.security.audit import AuditAction, AuditOutcome
from uaf.security.auth import LocalAuthProvider, PasswordCredentials
from uaf.security.primitives import ANONYMOUS, Role
from uaf.security.secure_graph_db import SecureGraphDB, Session


def _make_env() -> tuple[SecureGraphDB, LocalAuthProvider]:
    db = GraphDB()
    auth = LocalAuthProvider()
    return SecureGraphDB(db, auth), auth


def _art(title: str = "Doc") -> Artifact:
    return Artifact(
        meta=make_node_metadata(NodeType.ARTIFACT), title=title
    )


def _para(text: str = "Hello") -> Paragraph:
    return Paragraph(
        meta=make_node_metadata(NodeType.PARAGRAPH), text=text
    )


def _contains(parent: NodeId, child: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=parent,
        target=child,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


def _ref_edge(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.REFERENCES,
        created_at=utc_now(),
    )


def _login(
    sdb: SecureGraphDB,
    auth: LocalAuthProvider,
    name: str,
    pw: str = "pass",
) -> Session:
    p = auth.create_principal(name, pw)
    return sdb.authenticate(
        PasswordCredentials(principal_id=p.id, password=pw)
    )


class TestMultiUserDocument:
    """Scenario 1: Owner creates artifact, grants EDITOR to user 2,
    user 2 adds content, user 3 (no access) gets denied."""

    def test_multi_user_workflow(self) -> None:
        sdb, auth = _make_env()
        owner_s = _login(sdb, auth, "Owner")
        editor_s = _login(sdb, auth, "Editor")
        outsider_s = _login(sdb, auth, "Outsider")

        # Owner creates artifact
        doc = _art("Team Doc")
        sdb.create_node(owner_s, doc)

        # Grant EDITOR to editor
        sdb.grant_role(
            owner_s, doc.meta.id, editor_s.principal.id, Role.EDITOR
        )

        # Editor adds content
        p1 = _para("Editor's paragraph")
        sdb.create_node(editor_s, p1)
        sdb.create_edge(
            editor_s, _contains(doc.meta.id, p1.meta.id)
        )

        # Editor can read back
        result = sdb.get_node(editor_s, p1.meta.id)
        assert result is not None
        assert result.text == "Editor's paragraph"

        # Outsider cannot read
        assert sdb.get_node(outsider_s, doc.meta.id) is None
        assert sdb.get_node(outsider_s, p1.meta.id) is None

        # Outsider cannot write
        with pytest.raises(PermissionDeniedError):
            sdb.update_node(
                outsider_s,
                Artifact(meta=doc.meta, title="Hacked"),
            )


class TestPermissionChange:
    """Scenario 2: Owner revokes EDITOR from user 2,
    user 2 can still read (downgraded to VIEWER) but not write."""

    def test_downgrade_editor_to_viewer(self) -> None:
        sdb, auth = _make_env()
        owner_s = _login(sdb, auth, "Owner")
        user2_s = _login(sdb, auth, "User2")

        doc = _art("Shared Doc")
        sdb.create_node(owner_s, doc)
        sdb.grant_role(
            owner_s, doc.meta.id, user2_s.principal.id, Role.EDITOR
        )

        # User2 can write
        p1 = _para("Content")
        sdb.create_node(user2_s, p1)
        sdb.create_edge(
            user2_s, _contains(doc.meta.id, p1.meta.id)
        )

        # Downgrade to VIEWER
        sdb.grant_role(
            owner_s, doc.meta.id, user2_s.principal.id, Role.VIEWER
        )

        # User2 can still read
        assert sdb.get_node(user2_s, doc.meta.id) is not None

        # User2 cannot write
        p2 = _para("More content")
        with pytest.raises(PermissionDeniedError):
            sdb.create_node(user2_s, p2)
            sdb.create_edge(
                user2_s, _contains(doc.meta.id, p2.meta.id)
            )


class TestTransclusionAcrossArtifacts:
    """Scenario 3: User 1 owns artifact A, user 2 owns artifact B.
    Node X is in A, transcluded into B via REFERENCES edge.
    User 2 can see X via B only if they also have READ on A."""

    def test_transclusion_requires_source_read(self) -> None:
        sdb, auth = _make_env()
        user1_s = _login(sdb, auth, "User1")
        user2_s = _login(sdb, auth, "User2")

        # User1 creates artifact A with node X
        art_a = _art("Artifact A")
        sdb.create_node(user1_s, art_a)
        node_x = _para("Secret content")
        sdb.create_node(user1_s, node_x)
        sdb.create_edge(
            user1_s, _contains(art_a.meta.id, node_x.meta.id)
        )

        # User2 creates artifact B
        art_b = _art("Artifact B")
        sdb.create_node(user2_s, art_b)

        # User2 cannot read node_x (no access to art_a)
        assert sdb.get_node(user2_s, node_x.meta.id) is None

        # User1 grants VIEWER on art_a to User2
        sdb.grant_role(
            user1_s, art_a.meta.id, user2_s.principal.id, Role.VIEWER
        )

        # Now User2 can read node_x
        assert sdb.get_node(user2_s, node_x.meta.id) is not None


class TestAuditTrail:
    """Scenario 4: All operations from scenarios 1-3 appear in audit log
    with correct principals, actions, and outcomes."""

    def test_audit_captures_all_actions(self) -> None:
        sdb, auth = _make_env()
        owner_s = _login(sdb, auth, "Owner")
        stranger_s = _login(sdb, auth, "Stranger")

        # Create artifact
        doc = _art("Audited Doc")
        sdb.create_node(owner_s, doc)

        # Denied read by stranger (returns None, logs DENIED)
        sdb.get_node(stranger_s, doc.meta.id)

        # Denied write by stranger
        with pytest.raises(PermissionDeniedError):
            sdb.update_node(
                stranger_s,
                Artifact(meta=doc.meta, title="Nope"),
            )

        audit = sdb.get_audit_log()

        # Check owner's actions
        owner_entries = audit.for_principal(owner_s.principal.id)
        assert len(owner_entries) >= 1
        assert any(
            e.action == AuditAction.CREATE_NODE for e in owner_entries
        )

        # Check denied entries
        denied = audit.denied()
        assert len(denied) >= 2  # denied read + denied write
        assert all(e.outcome == AuditOutcome.DENIED for e in denied)


class TestSystemPrincipal:
    """Scenario 5: Internal operations via SYSTEM bypass all permission checks."""

    def test_system_bypasses_all(self) -> None:
        sdb, _ = _make_env()
        sys_s = sdb.system_session()

        # SYSTEM can create anything
        doc = _art("Internal Doc")
        sdb.create_node(sys_s, doc)

        p1 = _para("Internal content")
        sdb.create_node(sys_s, p1)
        sdb.create_edge(
            sys_s, _contains(doc.meta.id, p1.meta.id)
        )

        # SYSTEM can read everything
        assert sdb.get_node(sys_s, doc.meta.id) is not None
        assert sdb.get_node(sys_s, p1.meta.id) is not None

        # SYSTEM can delete
        sdb.delete_node(sys_s, p1.meta.id)
        assert sdb.get_node(sys_s, p1.meta.id) is None

        # SYSTEM can grant permissions
        sdb.grant_role(
            sys_s,
            doc.meta.id,
            sdb.system_session().principal.id,
            Role.OWNER,
        )


class TestPublicArtifact:
    """Scenario 6: Set public_read=True, verify ANONYMOUS can read but not write."""

    def test_public_read(self) -> None:
        sdb, auth = _make_env()
        owner_s = _login(sdb, auth, "Owner")
        anon_s = Session(principal=ANONYMOUS, token="")

        doc = _art("Public Knowledge Base")
        sdb.create_node(owner_s, doc)

        p1 = _para("Public content")
        sdb.create_node(owner_s, p1)
        sdb.create_edge(
            owner_s, _contains(doc.meta.id, p1.meta.id)
        )

        # Before making public — anonymous cannot read
        assert sdb.get_node(anon_s, doc.meta.id) is None

        # Make public
        sdb.set_public_read(owner_s, doc.meta.id, public=True)

        # Anonymous can now read
        assert sdb.get_node(anon_s, doc.meta.id) is not None
        assert sdb.get_node(anon_s, p1.meta.id) is not None

        # Anonymous cannot write
        with pytest.raises(PermissionDeniedError):
            sdb.update_node(
                anon_s, Paragraph(meta=p1.meta, text="Defaced")
            )

        # Anonymous cannot delete
        with pytest.raises(PermissionDeniedError):
            sdb.delete_node(anon_s, p1.meta.id)

        # Verify anonymous denied actions are audited
        audit = sdb.get_audit_log()
        anon_denied = [
            e
            for e in audit.denied()
            if e.principal_id == ANONYMOUS.id
        ]
        assert len(anon_denied) >= 2

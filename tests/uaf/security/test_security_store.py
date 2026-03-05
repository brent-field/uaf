"""Tests for SecurityStore — persist and replay security events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, utc_now
from uaf.core.nodes import Artifact, NodeType, Paragraph, make_node_metadata
from uaf.db.graph_db import GraphDB
from uaf.security.acl import PermissionResolver
from uaf.security.audit import AuditLog
from uaf.security.auth import LocalAuthProvider
from uaf.security.primitives import Role
from uaf.security.secure_graph_db import SecureGraphDB
from uaf.security.security_store import SecurityStore

if TYPE_CHECKING:
    from pathlib import Path


def _build_stack(
    tmp_path: Path,
) -> tuple[SecureGraphDB, SecurityStore, LocalAuthProvider]:
    """Build a full SecureGraphDB stack with SecurityStore wiring."""
    db = GraphDB()
    auth = LocalAuthProvider()
    resolver = PermissionResolver()
    audit = AuditLog()
    sec_store = SecurityStore(
        path=tmp_path / "security.jsonl",
        auth=auth,
        resolver=resolver,
        audit=audit,
    )
    sdb = SecureGraphDB(
        db, auth, on_security_event=sec_store.record
    )
    # Wire resolver and audit from the SecureGraphDB
    sdb._resolver = resolver
    sdb._audit = audit
    return sdb, sec_store, auth


class TestSecurityStorePrincipalPersistence:
    def test_principal_survives_replay(self, tmp_path: Path) -> None:
        _sdb, sec_store, auth = _build_stack(tmp_path)

        # Create a principal
        principal = auth.create_principal("alice", "password123", roles=frozenset({Role.OWNER}))
        # Manually record since create_principal is on auth, not SecureGraphDB
        sec_store.record_principal(
            principal, auth._password_hashes[principal.id]
        )
        sec_store.close()

        # Replay into fresh state
        auth2 = LocalAuthProvider()
        resolver2 = PermissionResolver()
        audit2 = AuditLog()
        sec_store2 = SecurityStore(
            path=tmp_path / "security.jsonl",
            auth=auth2,
            resolver=resolver2,
            audit=audit2,
        )
        sec_store2.replay()

        p = auth2.get_principal(principal.id)
        assert p is not None
        assert p.display_name == "alice"
        assert Role.OWNER in p.roles


class TestSecurityStoreACLPersistence:
    def test_acl_survives_replay(self, tmp_path: Path) -> None:
        sdb, sec_store, auth = _build_stack(tmp_path)

        # Create a principal and authenticate
        principal = auth.create_principal(
            "bob", "pass", roles=frozenset({Role.OWNER})
        )
        sec_store.record_principal(
            principal, auth._password_hashes[principal.id]
        )
        session = sdb.authenticate(
            __import__("uaf.security.auth", fromlist=["PasswordCredentials"]).PasswordCredentials(
                principal_id=principal.id, password="pass"
            )
        )

        # Create artifact (triggers ACL creation)
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT), title="Doc"
        )
        art_id = sdb.create_node(session, art)
        sec_store.close()

        # Replay into fresh state
        auth2 = LocalAuthProvider()
        resolver2 = PermissionResolver()
        audit2 = AuditLog()
        sec_store2 = SecurityStore(
            path=tmp_path / "security.jsonl",
            auth=auth2,
            resolver=resolver2,
            audit=audit2,
        )
        sec_store2.replay()

        # Check ACL was rebuilt
        acl = resolver2.get_acl(art_id)
        assert acl is not None
        assert len(acl.entries) == 1
        assert acl.entries[0].principal_id == principal.id
        assert acl.entries[0].role == Role.OWNER


class TestSecurityStoreParentPersistence:
    def test_parent_registration_survives_replay(self, tmp_path: Path) -> None:
        sdb, sec_store, auth = _build_stack(tmp_path)

        principal = auth.create_principal(
            "charlie", "pass", roles=frozenset({Role.OWNER})
        )
        sec_store.record_principal(
            principal, auth._password_hashes[principal.id]
        )
        from uaf.security.auth import PasswordCredentials

        session = sdb.authenticate(
            PasswordCredentials(principal_id=principal.id, password="pass")
        )

        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT), title="Doc"
        )
        art_id = sdb.create_node(session, art)

        para = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH),
            text="Hello",
            style="body",
        )
        sdb.create_node(session, para)

        edge = Edge(
            id=EdgeId.generate(),
            source=art_id,
            target=para.meta.id,
            edge_type=EdgeType.CONTAINS,
            created_at=utc_now(),
        )
        sdb.create_edge(session, edge)
        sec_store.close()

        # Replay into fresh state
        auth2 = LocalAuthProvider()
        resolver2 = PermissionResolver()
        audit2 = AuditLog()
        sec_store2 = SecurityStore(
            path=tmp_path / "security.jsonl",
            auth=auth2,
            resolver=resolver2,
            audit=audit2,
        )
        sec_store2.replay()

        # Check parent was rebuilt — find_artifact should trace to art_id
        found = resolver2.find_artifact(para.meta.id)
        assert found == art_id


class TestSecurityStoreAuditPersistence:
    def test_audit_entries_survive_replay(self, tmp_path: Path) -> None:
        sdb, sec_store, auth = _build_stack(tmp_path)

        principal = auth.create_principal(
            "dave", "pass", roles=frozenset({Role.OWNER})
        )
        sec_store.record_principal(
            principal, auth._password_hashes[principal.id]
        )
        from uaf.security.auth import PasswordCredentials

        session = sdb.authenticate(
            PasswordCredentials(principal_id=principal.id, password="pass")
        )

        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT), title="Audited"
        )
        sdb.create_node(session, art)
        sec_store.close()

        # Replay into fresh state
        auth2 = LocalAuthProvider()
        resolver2 = PermissionResolver()
        audit2 = AuditLog()
        sec_store2 = SecurityStore(
            path=tmp_path / "security.jsonl",
            auth=auth2,
            resolver=resolver2,
            audit=audit2,
        )
        sec_store2.replay()

        # Audit entries should be present
        entries = audit2.for_principal(principal.id)
        assert len(entries) > 0


class TestSecurityStoreCorruptLine:
    def test_corrupt_line_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "security.jsonl"
        path.write_text('NOT VALID JSON\n{"type":"register_artifact"}\n')

        auth = LocalAuthProvider()
        resolver = PermissionResolver()
        audit = AuditLog()
        sec_store = SecurityStore(
            path=path, auth=auth, resolver=resolver, audit=audit
        )
        # Should not raise
        sec_store.replay()

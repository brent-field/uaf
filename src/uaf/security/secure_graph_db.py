"""SecureGraphDB — security-enforcing wrapper around GraphDB."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from uaf.core.edges import EdgeType
from uaf.core.errors import PermissionDeniedError
from uaf.core.node_id import utc_now
from uaf.core.nodes import Artifact, NodeType
from uaf.core.operations import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    UpdateNode,
)
from uaf.security.acl import ACL, ACLEntry, PermissionResolver
from uaf.security.audit import AuditAction, AuditEntry, AuditLog, AuditOutcome
from uaf.security.primitives import SYSTEM, Permission, PrincipalId, Role

if TYPE_CHECKING:
    from uaf.core.edges import Edge
    from uaf.core.node_id import EdgeId, NodeId
    from uaf.db.graph_db import GraphDB
    from uaf.security.auth import AuthProvider, Credentials
    from uaf.security.primitives import Principal


@dataclass(frozen=True, slots=True)
class Session:
    """An authenticated session binding a principal to a token."""

    principal: Principal
    token: str


class SecureGraphDB:
    """Security-enforcing wrapper around GraphDB.

    All mutations require a Session and are checked against ACLs.
    All queries are filtered by permissions.
    All actions are logged to the audit trail.
    """

    def __init__(self, db: GraphDB, auth: AuthProvider) -> None:
        self._db = db
        self._auth = auth
        self._resolver = PermissionResolver()
        self._audit = AuditLog()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self, credentials: Credentials) -> Session:
        """Authenticate and return a session."""
        from uaf.security.auth import LocalAuthProvider

        principal = self._auth.authenticate(credentials)
        # Issue token if the provider supports it
        if isinstance(self._auth, LocalAuthProvider):
            token = self._auth.issue_token(principal)
        else:
            token = ""
        return Session(principal=principal, token=token)

    def system_session(self) -> Session:
        """Return a session for the SYSTEM principal (bypasses all checks)."""
        return Session(principal=SYSTEM, token="__system__")

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create_node(self, session: Session, node: Any) -> NodeId:
        """Create a node with permission checks and audit logging."""
        node_id: NodeId = node.meta.id
        artifact_id = self._resolver.find_artifact(node_id)

        # For new artifacts, the creator becomes OWNER
        if isinstance(node, Artifact):
            self._do_create_node(session, node)
            self._resolver.register_artifact(node_id)
            # Auto-create ACL with creator as OWNER
            acl = ACL(
                artifact_id=node_id,
                entries=(
                    ACLEntry(
                        principal_id=session.principal.id,
                        role=Role.OWNER,
                        granted_at=utc_now(),
                        granted_by=session.principal.id,
                    ),
                ),
            )
            self._resolver.set_acl(acl)
            self._record_audit(
                session, AuditAction.CREATE_NODE, node_id, node_id, AuditOutcome.ALLOWED
            )
            return node_id

        # For non-artifacts, check WRITE on the containing artifact
        if artifact_id is not None:
            self._check_permission(
                session, node_id, artifact_id, Permission.WRITE, AuditAction.CREATE_NODE
            )

        self._do_create_node(session, node)
        self._record_audit(
            session, AuditAction.CREATE_NODE, node_id, artifact_id, AuditOutcome.ALLOWED
        )
        return node_id

    def update_node(self, session: Session, node: Any) -> None:
        """Update a node with permission checks and audit logging."""
        node_id: NodeId = node.meta.id
        artifact_id = self._resolver.find_artifact(node_id)
        self._check_permission(
            session, node_id, artifact_id, Permission.WRITE, AuditAction.UPDATE_NODE
        )
        op = UpdateNode(
            node=node,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        self._db.apply(op)
        self._record_audit(
            session, AuditAction.UPDATE_NODE, node_id, artifact_id, AuditOutcome.ALLOWED
        )

    def delete_node(self, session: Session, node_id: NodeId) -> None:
        """Delete a node with permission checks and audit logging."""
        artifact_id = self._resolver.find_artifact(node_id)
        self._check_permission(
            session, node_id, artifact_id, Permission.DELETE, AuditAction.DELETE_NODE
        )
        op = DeleteNode(
            node_id=node_id,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        self._db.apply(op)
        self._record_audit(
            session, AuditAction.DELETE_NODE, node_id, artifact_id, AuditOutcome.ALLOWED
        )

    def create_edge(self, session: Session, edge: Edge) -> None:
        """Create an edge with permission checks and audit logging."""
        artifact_id = self._resolver.find_artifact(edge.source)
        self._check_permission(
            session, edge.source, artifact_id, Permission.WRITE, AuditAction.CREATE_EDGE
        )
        op = CreateEdge(
            edge=edge,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        self._db.apply(op)
        # Update the resolver's parent map for CONTAINS edges
        if edge.edge_type == EdgeType.CONTAINS:
            self._resolver.register_parent(edge.target, edge.source)
        self._record_audit(
            session, AuditAction.CREATE_EDGE, edge.source, artifact_id, AuditOutcome.ALLOWED
        )

    def delete_edge(self, session: Session, edge_id: EdgeId) -> None:
        """Delete an edge with permission checks and audit logging."""
        # Look up the edge to find its source for permission checking
        from uaf.core.node_id import NodeId as _NodeId

        edge = self._db._materializer.state.edges.get(edge_id)
        target_id: NodeId
        if edge is not None:
            target_id = edge.source
            artifact_id = self._resolver.find_artifact(edge.source)
        else:
            target_id = _NodeId.generate()
            artifact_id = None

        self._check_permission(
            session, target_id, artifact_id, Permission.WRITE, AuditAction.DELETE_EDGE
        )
        op = DeleteEdge(
            edge_id=edge_id,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        self._db.apply(op)
        self._record_audit(
            session, AuditAction.DELETE_EDGE, target_id, artifact_id, AuditOutcome.ALLOWED
        )

    # ------------------------------------------------------------------
    # Queries (filtered by permissions)
    # ------------------------------------------------------------------

    def get_node(self, session: Session, node_id: NodeId) -> Any | None:
        """Get a node, checking READ permission."""
        node = self._db.get_node(node_id)
        if node is None:
            return None

        artifact_id = self._resolver.find_artifact(node_id)
        if not self._resolver.resolve(session.principal, node_id, Permission.READ):
            self._record_audit(
                session, AuditAction.READ_NODE, node_id, artifact_id, AuditOutcome.DENIED
            )
            return None
        return node

    def get_children(self, session: Session, parent_id: NodeId) -> list[Any]:
        """Get children filtered by READ permission."""
        children = self._db.get_children(parent_id)
        return [
            c for c in children
            if self._resolver.resolve(session.principal, c.meta.id, Permission.READ)
        ]

    def find_by_type(self, session: Session, node_type: NodeType) -> list[Any]:
        """Find nodes by type, filtered by READ permission."""
        nodes = self._db.find_by_type(node_type)
        return [
            n for n in nodes
            if self._resolver.resolve(session.principal, n.meta.id, Permission.READ)
        ]

    # ------------------------------------------------------------------
    # Permission management
    # ------------------------------------------------------------------

    def grant_role(
        self,
        session: Session,
        artifact_id: NodeId,
        target_principal: PrincipalId,
        role: Role,
    ) -> None:
        """Grant a role to a principal on an artifact. Requires GRANT permission."""
        self._check_permission(
            session, artifact_id, artifact_id, Permission.GRANT, AuditAction.GRANT_PERMISSION
        )
        acl = self._resolver.get_acl(artifact_id)
        now = utc_now()
        new_entry = ACLEntry(
            principal_id=target_principal,
            role=role,
            granted_at=now,
            granted_by=session.principal.id,
        )
        if acl is None:
            acl = ACL(artifact_id=artifact_id, entries=(new_entry,))
        else:
            # Replace existing entry for this principal, or add new
            entries = tuple(e for e in acl.entries if e.principal_id != target_principal)
            acl = ACL(
                artifact_id=artifact_id,
                entries=(*entries, new_entry),
                default_role=acl.default_role,
                public_read=acl.public_read,
            )
        self._resolver.set_acl(acl)
        self._record_audit(
            session, AuditAction.GRANT_PERMISSION, artifact_id, artifact_id, AuditOutcome.ALLOWED
        )

    def revoke_role(
        self,
        session: Session,
        artifact_id: NodeId,
        target_principal: PrincipalId,
    ) -> None:
        """Revoke a principal's role on an artifact. Requires GRANT permission."""
        self._check_permission(
            session, artifact_id, artifact_id, Permission.GRANT, AuditAction.REVOKE_PERMISSION
        )
        acl = self._resolver.get_acl(artifact_id)
        if acl is not None:
            entries = tuple(e for e in acl.entries if e.principal_id != target_principal)
            acl = ACL(
                artifact_id=artifact_id,
                entries=entries,
                default_role=acl.default_role,
                public_read=acl.public_read,
            )
            self._resolver.set_acl(acl)
        self._record_audit(
            session,
            AuditAction.REVOKE_PERMISSION,
            artifact_id,
            artifact_id,
            AuditOutcome.ALLOWED,
        )

    def get_acl(self, session: Session, artifact_id: NodeId) -> ACL | None:
        """Get the ACL for an artifact. Requires READ permission."""
        if not self._resolver.resolve(session.principal, artifact_id, Permission.READ):
            return None
        return self._resolver.get_acl(artifact_id)

    def set_public_read(self, session: Session, artifact_id: NodeId, *, public: bool) -> None:
        """Set public read on an artifact. Requires ADMIN permission."""
        self._check_permission(
            session, artifact_id, artifact_id, Permission.ADMIN, AuditAction.GRANT_PERMISSION
        )
        acl = self._resolver.get_acl(artifact_id)
        if acl is not None:
            acl = ACL(
                artifact_id=artifact_id,
                entries=acl.entries,
                default_role=acl.default_role,
                public_read=public,
            )
            self._resolver.set_acl(acl)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def get_audit_log(self) -> AuditLog:
        """Return the audit log for querying."""
        return self._audit

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_create_node(self, session: Session, node: Any) -> None:
        """Create a node in the underlying GraphDB with principal tracking."""
        op = CreateNode(
            node=node,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        self._db.apply(op)

    def _check_permission(
        self,
        session: Session,
        node_id: NodeId,
        artifact_id: NodeId | None,
        permission: Permission,
        action: AuditAction,
    ) -> None:
        """Check permission and raise PermissionDeniedError if denied."""
        if not self._resolver.resolve(session.principal, node_id, permission):
            self._record_audit(session, action, node_id, artifact_id, AuditOutcome.DENIED)
            msg = (
                f"Principal {session.principal.id} lacks {permission.value} "
                f"on node {node_id}"
            )
            raise PermissionDeniedError(msg)

    def _record_audit(
        self,
        session: Session,
        action: AuditAction,
        target_id: NodeId,
        artifact_id: NodeId | None,
        outcome: AuditOutcome,
    ) -> None:
        """Record an audit entry."""
        self._audit.record(
            AuditEntry(
                operation_id=None,
                principal_id=session.principal.id,
                timestamp=utc_now(),
                action=action,
                target_id=target_id,
                artifact_id=artifact_id,
                outcome=outcome,
            )
        )

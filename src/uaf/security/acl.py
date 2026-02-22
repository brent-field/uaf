"""ACL model — ACLEntry, ACL, NodePermissionOverride, and PermissionResolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from uaf.security.primitives import (
    ROLE_PERMISSIONS,
    SYSTEM,
    Permission,
    Role,
)

if TYPE_CHECKING:
    from datetime import datetime

    from uaf.core.node_id import NodeId
    from uaf.security.primitives import Principal, PrincipalId


@dataclass(frozen=True, slots=True)
class ACLEntry:
    """A single permission grant: principal → role on an artifact."""

    principal_id: PrincipalId
    role: Role
    granted_at: datetime
    granted_by: PrincipalId


@dataclass(frozen=True, slots=True)
class ACL:
    """Per-artifact access control list."""

    artifact_id: NodeId
    entries: tuple[ACLEntry, ...]
    default_role: Role | None = None
    public_read: bool = False


@dataclass(frozen=True, slots=True)
class NodePermissionOverride:
    """Node-level permission override — restricts or expands permissions for specific nodes."""

    node_id: NodeId
    entries: tuple[ACLEntry, ...]


def _find_entry(entries: tuple[ACLEntry, ...], principal_id: PrincipalId) -> ACLEntry | None:
    """Find the ACLEntry for a given principal, or None."""
    for entry in entries:
        if entry.principal_id == principal_id:
            return entry
    return None


class PermissionResolver:
    """Resolves effective permissions for a principal on a node.

    Maintains a cache of ACLs and node overrides, plus a parent map
    for walking up the containment tree to find artifact roots.
    """

    def __init__(self) -> None:
        self._acls: dict[NodeId, ACL] = {}
        self._overrides: dict[NodeId, NodePermissionOverride] = {}
        # Maps child NodeId → parent NodeId (built from CONTAINS edges)
        self._parent_map: dict[NodeId, NodeId] = {}
        # Set of known artifact NodeIds
        self._artifacts: set[NodeId] = set()

    def set_acl(self, acl: ACL) -> None:
        """Register or update an ACL for an artifact."""
        self._acls[acl.artifact_id] = acl

    def remove_acl(self, artifact_id: NodeId) -> None:
        """Remove an ACL."""
        self._acls.pop(artifact_id, None)

    def get_acl(self, artifact_id: NodeId) -> ACL | None:
        """Get the ACL for an artifact."""
        return self._acls.get(artifact_id)

    def set_override(self, override: NodePermissionOverride) -> None:
        """Register a node-level permission override."""
        self._overrides[override.node_id] = override

    def remove_override(self, node_id: NodeId) -> None:
        """Remove a node override."""
        self._overrides.pop(node_id, None)

    def register_parent(self, child_id: NodeId, parent_id: NodeId) -> None:
        """Record a containment relationship (CONTAINS edge)."""
        self._parent_map[child_id] = parent_id

    def unregister_parent(self, child_id: NodeId) -> None:
        """Remove a containment relationship."""
        self._parent_map.pop(child_id, None)

    def register_artifact(self, artifact_id: NodeId) -> None:
        """Mark a node as an artifact (root of a containment tree)."""
        self._artifacts.add(artifact_id)

    def unregister_artifact(self, artifact_id: NodeId) -> None:
        """Remove an artifact registration."""
        self._artifacts.discard(artifact_id)

    def find_artifact(self, node_id: NodeId) -> NodeId | None:
        """Walk up CONTAINS edges to find the artifact root containing this node."""
        current = node_id
        visited: set[NodeId] = set()
        while current not in visited:
            if current in self._artifacts:
                return current
            visited.add(current)
            parent = self._parent_map.get(current)
            if parent is None:
                return None
            current = parent
        return None

    def resolve(self, principal: Principal, node_id: NodeId, action: Permission) -> bool:
        """Check if a principal has the given permission on a node."""
        # 1. SYSTEM bypasses all checks
        if principal is SYSTEM:
            return True

        # 2. Check node-level override first
        override = self._overrides.get(node_id)
        if override is not None:
            entry = _find_entry(override.entries, principal.id)
            if entry is not None:
                return action in ROLE_PERMISSIONS[entry.role]

        # 3. Walk up to artifact root, get ACL
        artifact_id = self.find_artifact(node_id)
        if artifact_id is None:
            return False

        acl = self._acls.get(artifact_id)
        if acl is None:
            return False

        # 4. Check principal's explicit role
        entry = _find_entry(acl.entries, principal.id)
        if entry is not None:
            return action in ROLE_PERMISSIONS[entry.role]

        # 5. Check default role
        if acl.default_role is not None:
            return action in ROLE_PERMISSIONS[acl.default_role]

        # 6. Check public read
        return acl.public_read and action == Permission.READ

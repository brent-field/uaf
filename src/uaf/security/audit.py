"""Audit log — AuditEntry, AuditAction, AuditOutcome, and AuditLog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from uaf.core.node_id import NodeId, OperationId
    from uaf.security.primitives import PrincipalId


@unique
class AuditAction(Enum):
    """Types of auditable actions."""

    CREATE_NODE = "create_node"
    UPDATE_NODE = "update_node"
    DELETE_NODE = "delete_node"
    CREATE_EDGE = "create_edge"
    DELETE_EDGE = "delete_edge"
    MOVE_NODE = "move_node"
    GRANT_PERMISSION = "grant_permission"
    REVOKE_PERMISSION = "revoke_permission"
    READ_NODE = "read_node"
    QUERY = "query"
    UNDO = "undo"
    REDO = "redo"


@unique
class AuditOutcome(Enum):
    """Whether the action was allowed or denied."""

    ALLOWED = "allowed"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """A single audit log record."""

    operation_id: OperationId | None
    principal_id: PrincipalId
    timestamp: datetime
    action: AuditAction
    target_id: NodeId
    artifact_id: NodeId | None
    outcome: AuditOutcome


class AuditLog:
    """Append-only audit log with indexed queries."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._by_principal: dict[PrincipalId, list[AuditEntry]] = {}
        self._by_node: dict[NodeId, list[AuditEntry]] = {}
        self._by_artifact: dict[NodeId, list[AuditEntry]] = {}
        self._denied: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        """Append an audit entry and update indexes."""
        self._entries.append(entry)
        self._by_principal.setdefault(entry.principal_id, []).append(entry)
        self._by_node.setdefault(entry.target_id, []).append(entry)
        if entry.artifact_id is not None:
            self._by_artifact.setdefault(entry.artifact_id, []).append(entry)
        if entry.outcome == AuditOutcome.DENIED:
            self._denied.append(entry)

    def for_principal(
        self, principal_id: PrincipalId, *, since: datetime | None = None
    ) -> list[AuditEntry]:
        """All actions by a principal, optionally filtered by time."""
        entries = self._by_principal.get(principal_id, [])
        if since is not None:
            return [e for e in entries if e.timestamp >= since]
        return list(entries)

    def for_node(
        self, node_id: NodeId, *, since: datetime | None = None
    ) -> list[AuditEntry]:
        """All actions on a node, optionally filtered by time."""
        entries = self._by_node.get(node_id, [])
        if since is not None:
            return [e for e in entries if e.timestamp >= since]
        return list(entries)

    def for_artifact(
        self, artifact_id: NodeId, *, since: datetime | None = None
    ) -> list[AuditEntry]:
        """All actions within an artifact, optionally filtered by time."""
        entries = self._by_artifact.get(artifact_id, [])
        if since is not None:
            return [e for e in entries if e.timestamp >= since]
        return list(entries)

    def denied(self, *, since: datetime | None = None) -> list[AuditEntry]:
        """All denied actions, optionally filtered by time."""
        if since is not None:
            return [e for e in self._denied if e.timestamp >= since]
        return list(self._denied)

    def count(self) -> int:
        """Total number of audit entries."""
        return len(self._entries)

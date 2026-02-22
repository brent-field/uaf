"""Operation types — immutable mutation records forming the operation DAG."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from uaf.core.errors import SerializationError
from uaf.core.serialization import (
    content_hash,
    edge_from_dict,
    edge_to_dict,
    node_from_dict,
    node_to_dict,
)

if TYPE_CHECKING:
    from datetime import datetime

    from uaf.core.edges import Edge
    from uaf.core.node_id import NodeId, OperationId


# ---------------------------------------------------------------------------
# Operation dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateNode:
    """Add a node to the graph."""

    node: Any  # NodeData
    parent_ops: tuple[OperationId, ...]
    timestamp: datetime
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateNode:
    """Replace a node's data (full state, not diff)."""

    node: Any  # NodeData
    parent_ops: tuple[OperationId, ...]
    timestamp: datetime
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeleteNode:
    """Mark a node as deleted."""

    node_id: NodeId
    parent_ops: tuple[OperationId, ...]
    timestamp: datetime
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreateEdge:
    """Add an edge to the graph."""

    edge: Edge
    parent_ops: tuple[OperationId, ...]
    timestamp: datetime
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeleteEdge:
    """Remove an edge."""

    edge_id: Any  # EdgeId
    parent_ops: tuple[OperationId, ...]
    timestamp: datetime
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class MoveNode:
    """Re-parent a node in the containment tree."""

    node_id: NodeId
    new_parent_id: NodeId
    parent_ops: tuple[OperationId, ...]
    timestamp: datetime
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReorderChildren:
    """Reorder the children of a parent node."""

    parent_id: NodeId
    new_order: tuple[NodeId, ...]
    parent_ops: tuple[OperationId, ...]
    timestamp: datetime
    principal_id: str | None = None


type Operation = (
    CreateNode | UpdateNode | DeleteNode | CreateEdge | DeleteEdge | MoveNode | ReorderChildren
)


# ---------------------------------------------------------------------------
# Operation serialization
# ---------------------------------------------------------------------------

_OP_TYPE_NAME: dict[type[Any], str] = {
    CreateNode: "CreateNode",
    UpdateNode: "UpdateNode",
    DeleteNode: "DeleteNode",
    CreateEdge: "CreateEdge",
    DeleteEdge: "DeleteEdge",
    MoveNode: "MoveNode",
    ReorderChildren: "ReorderChildren",
}


def operation_to_dict(op: Operation) -> dict[str, Any]:
    """Serialize an Operation to a dict."""
    type_name = _OP_TYPE_NAME.get(type(op))
    if type_name is None:
        msg = f"Unknown operation type: {type(op)}"
        raise SerializationError(msg)

    d: dict[str, Any] = {
        "__type__": type_name,
        "parent_ops": [str(pid) for pid in op.parent_ops],
        "timestamp": op.timestamp.isoformat(),
        "principal_id": op.principal_id,
    }

    match op:
        case CreateNode(node=node):
            d["node"] = node_to_dict(node)
        case UpdateNode(node=node):
            d["node"] = node_to_dict(node)
        case DeleteNode(node_id=node_id):
            d["node_id"] = str(node_id)
        case CreateEdge(edge=edge):
            d["edge"] = edge_to_dict(edge)
        case DeleteEdge(edge_id=edge_id):
            d["edge_id"] = str(edge_id)
        case MoveNode(node_id=node_id, new_parent_id=new_parent_id):
            d["node_id"] = str(node_id)
            d["new_parent_id"] = str(new_parent_id)
        case ReorderChildren(parent_id=parent_id, new_order=new_order):
            d["parent_id"] = str(parent_id)
            d["new_order"] = [str(nid) for nid in new_order]

    return d


def operation_from_dict(d: dict[str, Any]) -> Operation:
    """Deserialize a dict to an Operation."""
    import uuid
    from datetime import datetime

    from uaf.core.node_id import EdgeId, NodeId, OperationId

    type_name = d.get("__type__")
    if type_name is None:
        msg = "Missing '__type__' in serialized operation"
        raise SerializationError(msg)

    parent_ops = tuple(OperationId(hex_digest=h) for h in d["parent_ops"])
    timestamp = datetime.fromisoformat(d["timestamp"])
    principal_id: str | None = d.get("principal_id")

    match type_name:
        case "CreateNode":
            return CreateNode(
                node=node_from_dict(d["node"]),
                parent_ops=parent_ops,
                timestamp=timestamp,
                principal_id=principal_id,
            )
        case "UpdateNode":
            return UpdateNode(
                node=node_from_dict(d["node"]),
                parent_ops=parent_ops,
                timestamp=timestamp,
                principal_id=principal_id,
            )
        case "DeleteNode":
            return DeleteNode(
                node_id=NodeId(value=uuid.UUID(d["node_id"])),
                parent_ops=parent_ops,
                timestamp=timestamp,
                principal_id=principal_id,
            )
        case "CreateEdge":
            return CreateEdge(
                edge=edge_from_dict(d["edge"]),
                parent_ops=parent_ops,
                timestamp=timestamp,
                principal_id=principal_id,
            )
        case "DeleteEdge":
            return DeleteEdge(
                edge_id=EdgeId(value=uuid.UUID(d["edge_id"])),
                parent_ops=parent_ops,
                timestamp=timestamp,
                principal_id=principal_id,
            )
        case "MoveNode":
            return MoveNode(
                node_id=NodeId(value=uuid.UUID(d["node_id"])),
                new_parent_id=NodeId(value=uuid.UUID(d["new_parent_id"])),
                parent_ops=parent_ops,
                timestamp=timestamp,
                principal_id=principal_id,
            )
        case "ReorderChildren":
            return ReorderChildren(
                parent_id=NodeId(value=uuid.UUID(d["parent_id"])),
                new_order=tuple(NodeId(value=uuid.UUID(n)) for n in d["new_order"]),
                parent_ops=parent_ops,
                timestamp=timestamp,
                principal_id=principal_id,
            )
        case _:
            msg = f"Unknown operation type: {type_name}"
            raise SerializationError(msg)


def compute_operation_id(op: Operation) -> OperationId:
    """Compute the content-addressed OperationId (SHA-256 of canonical JSON)."""
    return content_hash(operation_to_dict(op))

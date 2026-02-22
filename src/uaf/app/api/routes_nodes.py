"""Node CRUD and query endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from uaf.app.api.dependencies import get_db, get_session
from uaf.app.api.schemas import (
    ChildrenResponse,
    HistoryEntryResponse,
    HistoryResponse,
    NodeResponse,
    SearchResponse,
)
from uaf.core.errors import PermissionDeniedError
from uaf.core.node_id import NodeId
from uaf.core.nodes import NodeType
from uaf.security.secure_graph_db import SecureGraphDB, Session

router = APIRouter()


def _node_to_response(node: object) -> NodeResponse:
    """Convert a graph node to a NodeResponse."""
    from uaf.core.serialization import node_to_dict

    d = node_to_dict(node)
    meta = d.pop("meta", {})
    node_type = meta.get("node_type", "unknown")
    return NodeResponse(
        id=meta.get("id", ""),
        node_type=node_type,
        fields=d,
        created_at=meta.get("created_at", ""),
        updated_at=meta.get("updated_at", ""),
    )


@router.get("/{node_id}", response_model=NodeResponse)
def get_node(
    node_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> NodeResponse:
    """Get a single node."""
    nid = NodeId(value=uuid.UUID(node_id))
    node = db.get_node(session, nid)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return _node_to_response(node)


@router.delete("/{node_id}", status_code=204)
def delete_node(
    node_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> None:
    """Delete a node."""
    nid = NodeId(value=uuid.UUID(node_id))
    try:
        db.delete_node(session, nid)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.get("/{node_id}/children", response_model=ChildrenResponse)
def get_children(
    node_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> ChildrenResponse:
    """Get ordered children of a node."""
    nid = NodeId(value=uuid.UUID(node_id))
    children = db.get_children(session, nid)
    return ChildrenResponse(
        parent_id=node_id,
        children=[_node_to_response(c) for c in children],
    )


@router.get("/{node_id}/history", response_model=HistoryResponse)
def get_history(
    node_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> HistoryResponse:
    """Get operation history for a node."""
    nid = NodeId(value=uuid.UUID(node_id))
    entries = db._db.get_history(nid)
    items: list[HistoryEntryResponse] = []
    for entry in entries:
        op = entry.operation
        items.append(HistoryEntryResponse(
            operation_id=str(entry.operation_id),
            operation_type=type(op).__name__,
            timestamp=op.timestamp,
            principal_id=op.principal_id,
        ))
    return HistoryResponse(node_id=node_id, entries=items)


# ---------------------------------------------------------------------------
# Search endpoints (mounted separately but logically belong with nodes)
# ---------------------------------------------------------------------------

search_router = APIRouter()


@search_router.get("", response_model=SearchResponse)
def search(
    type: str | None = None,
    attr: str | None = None,
    val: str | None = None,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> SearchResponse:
    """Search for nodes by type or attribute."""
    if type is not None:
        try:
            nt = NodeType(type.lower())
        except ValueError:
            raise HTTPException(  # noqa: B904
                status_code=400, detail=f"Unknown node type: {type}"
            )
        nodes = db.find_by_type(session, nt)
        return SearchResponse(results=[_node_to_response(n) for n in nodes])

    if attr is not None and val is not None:
        nodes = db._db.find_by_attribute(attr, val)
        return SearchResponse(results=[_node_to_response(n) for n in nodes])

    return SearchResponse(results=[])

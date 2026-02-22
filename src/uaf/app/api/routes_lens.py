"""Lens render and action endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from uaf.app.api.dependencies import get_db, get_registry, get_session
from uaf.app.api.schemas import LensActionRequest, LensViewResponse
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.actions import (
    DeleteColumn,
    DeleteNode,
    DeleteRow,
    DeleteText,
    FormatText,
    InsertColumn,
    InsertRow,
    InsertText,
    MoveNode,
    RenameArtifact,
    ReorderNodes,
    SetCellValue,
)
from uaf.core.node_id import NodeId
from uaf.security.secure_graph_db import SecureGraphDB, Session

router = APIRouter()


@router.get("/{artifact_id}/lens/{lens_type}", response_model=LensViewResponse)
def render_lens(
    artifact_id: str,
    lens_type: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
    registry: LensRegistry = Depends(get_registry),
) -> LensViewResponse:
    """Render an artifact through a lens."""
    lens = registry.get(lens_type)
    if lens is None:
        raise HTTPException(status_code=404, detail=f"Unknown lens type: {lens_type}")

    aid = NodeId(value=uuid.UUID(artifact_id))
    view = lens.render(db, session, aid)
    return LensViewResponse(
        lens_type=view.lens_type,
        artifact_id=str(view.artifact_id),
        title=view.title,
        content=view.content,
        content_type=view.content_type,
        node_count=view.node_count,
    )


@router.post("/{artifact_id}/lens/{lens_type}/action", status_code=204)
def apply_lens_action(
    artifact_id: str,
    lens_type: str,
    body: LensActionRequest,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
    registry: LensRegistry = Depends(get_registry),
) -> None:
    """Apply a lens action to an artifact."""
    lens = registry.get(lens_type)
    if lens is None:
        raise HTTPException(status_code=404, detail=f"Unknown lens type: {lens_type}")

    aid = NodeId(value=uuid.UUID(artifact_id))
    action = _parse_action(body)
    lens.apply_action(db, session, aid, action)


def _parse_action(body: LensActionRequest) -> (
    InsertText | DeleteText | FormatText | SetCellValue
    | InsertRow | InsertColumn | DeleteRow | DeleteColumn
    | ReorderNodes | MoveNode | DeleteNode | RenameArtifact
):
    """Parse a LensActionRequest into a typed LensAction."""
    params = body.params
    at = body.action_type

    def _nid(key: str) -> NodeId:
        return NodeId(value=uuid.UUID(params[key]))

    match at:
        case "insert_text":
            return InsertText(
                parent_id=_nid("parent_id"),
                text=params["text"],
                position=params.get("position", 0),
                style=params.get("style", "paragraph"),
            )
        case "delete_text":
            return DeleteText(node_id=_nid("node_id"))
        case "format_text":
            return FormatText(
                node_id=_nid("node_id"),
                style=params["style"],
                level=params.get("level", 1),
            )
        case "set_cell_value":
            return SetCellValue(cell_id=_nid("cell_id"), value=params["value"])
        case "insert_row":
            return InsertRow(sheet_id=_nid("sheet_id"), position=params["position"])
        case "insert_column":
            return InsertColumn(sheet_id=_nid("sheet_id"), position=params["position"])
        case "delete_row":
            return DeleteRow(sheet_id=_nid("sheet_id"), position=params["position"])
        case "delete_column":
            return DeleteColumn(sheet_id=_nid("sheet_id"), position=params["position"])
        case "reorder_nodes":
            order = tuple(NodeId(value=uuid.UUID(n)) for n in params["new_order"])
            return ReorderNodes(parent_id=_nid("parent_id"), new_order=order)
        case "move_node":
            return MoveNode(node_id=_nid("node_id"), new_parent_id=_nid("new_parent_id"))
        case "delete_node":
            return DeleteNode(node_id=_nid("node_id"))
        case "rename_artifact":
            return RenameArtifact(artifact_id=_nid("artifact_id"), title=params["title"])
        case _:
            msg = f"Unknown action type: {at}"
            raise HTTPException(status_code=400, detail=msg)

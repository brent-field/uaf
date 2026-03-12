"""Undo / Redo / Revert REST API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from uaf.app.api.dependencies import get_db, get_session
from uaf.security.secure_graph_db import SecureGraphDB, Session

router = APIRouter()


class UndoRedoResponse(BaseModel):
    """Response for undo/redo operations."""

    operation_ids: list[str]


class RevertRequest(BaseModel):
    """Request body for revert-to-timestamp."""

    target_time: datetime


@router.post("/{artifact_id}/undo", response_model=UndoRedoResponse)
def undo(
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> UndoRedoResponse:
    """Undo the most recent action group."""
    op_ids = db.undo(session)
    return UndoRedoResponse(operation_ids=[str(oid) for oid in op_ids])


@router.post("/{artifact_id}/redo", response_model=UndoRedoResponse)
def redo(
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> UndoRedoResponse:
    """Redo the most recently undone action group."""
    op_ids = db.redo(session)
    return UndoRedoResponse(operation_ids=[str(oid) for oid in op_ids])


@router.post("/{artifact_id}/revert", response_model=UndoRedoResponse)
def revert(
    artifact_id: str,
    body: RevertRequest,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> UndoRedoResponse:
    """Revert the graph to a specific point in time."""
    from uaf.db.undo import compute_revert_ops

    inner_db: Any = db._db
    log = inner_db._log
    state = inner_db._materializer.state
    principal_id = session.principal.id.value

    ops = compute_revert_ops(body.target_time, log, state, principal_id)
    if not ops:
        return UndoRedoResponse(operation_ids=[])

    result_ids: list[str] = []
    with db.action_group(session):
        for op in ops:
            try:
                op_id = inner_db.apply(op)
                result_ids.append(str(op_id))
            except Exception:
                continue

    return UndoRedoResponse(operation_ids=result_ids)

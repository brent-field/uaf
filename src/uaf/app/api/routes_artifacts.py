"""Artifact CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from uaf.app.api.dependencies import get_db, get_session
from uaf.app.api.schemas import (
    ArtifactListResponse,
    ArtifactResponse,
    CreateArtifactRequest,
)
from uaf.core.errors import PermissionDeniedError
from uaf.core.nodes import Artifact, NodeType, make_node_metadata
from uaf.security.secure_graph_db import SecureGraphDB, Session

router = APIRouter()


@router.post("", response_model=ArtifactResponse, status_code=201)
def create_artifact(
    body: CreateArtifactRequest,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> ArtifactResponse:
    """Create a new artifact."""
    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=body.title)
    art_id = db.create_node(session, art)
    return ArtifactResponse(
        id=str(art_id),
        title=art.title,
        created_at=art.meta.created_at,
        updated_at=art.meta.updated_at,
    )


@router.get("", response_model=ArtifactListResponse)
def list_artifacts(
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> ArtifactListResponse:
    """List all visible artifacts."""
    artifacts = db.find_by_type(session, NodeType.ARTIFACT)
    items: list[ArtifactResponse] = []
    for art in artifacts:
        if isinstance(art, Artifact):
            children = db.get_children(session, art.meta.id)
            items.append(ArtifactResponse(
                id=str(art.meta.id),
                title=art.title,
                created_at=art.meta.created_at,
                updated_at=art.meta.updated_at,
                child_count=len(children),
            ))
    return ArtifactListResponse(artifacts=items)


@router.get("/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> ArtifactResponse:
    """Get a single artifact."""
    import uuid

    from uaf.core.node_id import NodeId

    node_id = NodeId(value=uuid.UUID(artifact_id))
    art = db.get_node(session, node_id)
    if art is None or not isinstance(art, Artifact):
        raise HTTPException(status_code=404, detail="Artifact not found")
    children = db.get_children(session, node_id)
    return ArtifactResponse(
        id=str(node_id),
        title=art.title,
        created_at=art.meta.created_at,
        updated_at=art.meta.updated_at,
        child_count=len(children),
    )


@router.delete("/{artifact_id}", status_code=204)
def delete_artifact(
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> None:
    """Delete an artifact."""
    import uuid

    from uaf.core.node_id import NodeId

    node_id = NodeId(value=uuid.UUID(artifact_id))
    try:
        db.delete_node(session, node_id)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

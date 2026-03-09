"""Bundle export/import and sharing endpoints."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from uaf.app.api.dependencies import get_db, get_session
from uaf.core.node_id import NodeId
from uaf.core.nodes import Artifact
from uaf.db.bundle import export_bundle, import_bundle
from uaf.security.secure_graph_db import SecureGraphDB, Session

router = APIRouter()


class ImportBundleResponse(BaseModel):
    imported_ids: list[str]


@router.get("/artifacts/{artifact_id}/bundle")
def export_artifact_bundle(
    artifact_id: str,
    snapshot: bool = False,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> FileResponse:
    """Export an artifact as a .uaf bundle."""
    aid = NodeId(value=uuid.UUID(artifact_id))
    art = db.get_node(session, aid)
    if art is None or not isinstance(art, Artifact):
        raise HTTPException(status_code=404, detail="Artifact not found")

    with tempfile.NamedTemporaryFile(suffix=".uaf", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    export_bundle(db._db, [aid], tmp_path, snapshot=snapshot)

    title = art.title.replace(" ", "_")
    return FileResponse(
        path=str(tmp_path),
        media_type="application/zip",
        filename=f"{title}.uaf",
    )


@router.post("/artifacts/import-bundle", response_model=ImportBundleResponse, status_code=201)
def import_artifact_bundle(
    file: UploadFile,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> ImportBundleResponse:
    """Import a .uaf bundle, creating new artifacts."""
    with tempfile.NamedTemporaryFile(suffix=".uaf", delete=False) as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        imported_ids = import_bundle(db._db, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    # Register imported artifacts in the security layer
    for art_id in imported_ids:
        _register_imported_artifact(db, session, art_id)

    return ImportBundleResponse(
        imported_ids=[str(aid) for aid in imported_ids],
    )


def _register_imported_artifact(
    db: SecureGraphDB, session: Session, art_id: NodeId,
) -> None:
    """Register an imported artifact and its children in the security layer."""
    from uaf.core.node_id import utc_now
    from uaf.security.acl import ACL, ACLEntry
    from uaf.security.primitives import Role

    resolver = db._resolver
    resolver.register_artifact(art_id)
    acl = ACL(
        artifact_id=art_id,
        entries=(
            ACLEntry(
                principal_id=session.principal.id,
                role=Role.OWNER,
                granted_at=utc_now(),
                granted_by=session.principal.id,
            ),
        ),
    )
    resolver.set_acl(acl)

    # Register parent mappings for children
    children = db._db.get_children(art_id)
    for child in children:
        resolver.register_parent(child.meta.id, art_id)
        grandchildren = db._db.get_children(child.meta.id)
        for gc in grandchildren:
            resolver.register_parent(gc.meta.id, child.meta.id)

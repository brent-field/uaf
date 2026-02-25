"""File import/export endpoints."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from uaf.app.api.dependencies import get_db, get_session
from uaf.app.api.schemas import ArtifactResponse
from uaf.app.formats.csv_format import CsvHandler
from uaf.app.formats.docx_format import DocxHandler
from uaf.app.formats.gdoc_format import GdocHandler
from uaf.app.formats.markdown import MarkdownHandler
from uaf.app.formats.pdf_format import PdfHandler
from uaf.app.formats.plaintext import PlainTextHandler
from uaf.core.node_id import NodeId
from uaf.core.nodes import Artifact
from uaf.security.secure_graph_db import SecureGraphDB, Session

router = APIRouter()

_HANDLERS: dict[
    str,
    MarkdownHandler | CsvHandler | PlainTextHandler | DocxHandler | PdfHandler | GdocHandler,
] = {
    "markdown": MarkdownHandler(),
    "csv": CsvHandler(),
    "plaintext": PlainTextHandler(),
    "docx": DocxHandler(),
    "pdf": PdfHandler(),
    "gdoc": GdocHandler(),
}

_EXTENSIONS: dict[str, str] = {
    "markdown": ".md",
    "csv": ".csv",
    "plaintext": ".txt",
    "docx": ".docx",
    "pdf": ".pdf",
    "gdoc": ".json",
}
_EXT_TO_FORMAT: dict[str, str] = {v: k for k, v in _EXTENSIONS.items()}
_EXT_TO_FORMAT[".gdoc"] = "gdoc"


@router.post("/artifacts/import", response_model=ArtifactResponse, status_code=201)
def import_file(
    file: UploadFile,
    format: str | None = None,
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> ArtifactResponse:
    """Import a file into the graph. Format is auto-detected from the file extension."""
    original_name = file.filename or "upload.txt"
    suffix = Path(original_name).suffix.lower()

    # Auto-detect format from extension, allow explicit override
    fmt = format if format is not None else _EXT_TO_FORMAT.get(suffix)
    handler = _HANDLERS.get(fmt) if fmt else None
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    stem = Path(original_name).stem
    with tempfile.NamedTemporaryFile(
        prefix=f"{stem}_", suffix=suffix, delete=False,
    ) as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    # Rename to preserve original stem (handlers use path.stem as artifact title).
    # Use a unique subdir to avoid collisions from repeated imports.
    import_dir = tmp_path.parent / f"uaf_import_{uuid.uuid4().hex[:8]}"
    import_dir.mkdir()
    final_path = import_dir / f"{stem}{suffix}"
    tmp_path.rename(final_path)

    try:
        # Import uses raw GraphDB (format handlers expect GraphDB, not SecureGraphDB)
        art_id = handler.import_file(final_path, db._db)
    finally:
        final_path.unlink(missing_ok=True)
        import_dir.rmdir()

    art = db._db.get_node(art_id)
    if art is None or not isinstance(art, Artifact):
        raise HTTPException(status_code=500, detail="Import failed")

    # Register the imported artifact and its children in the security layer
    _register_imported_artifact(db, session, art_id)

    return ArtifactResponse(
        id=str(art_id),
        title=art.title,
        created_at=art.meta.created_at,
        updated_at=art.meta.updated_at,
    )


@router.get("/artifacts/{artifact_id}/export")
def export_file(
    artifact_id: str,
    format: str = "markdown",
    db: SecureGraphDB = Depends(get_db),
    session: Session = Depends(get_session),
) -> FileResponse:
    """Export an artifact as a file."""
    handler = _HANDLERS.get(format)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown format: {format}")

    aid = NodeId(value=uuid.UUID(artifact_id))
    art = db.get_node(session, aid)
    if art is None or not isinstance(art, Artifact):
        raise HTTPException(status_code=404, detail="Artifact not found")

    suffix = _EXTENSIONS.get(format, ".txt")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    handler.export_file(db._db, aid, tmp_path)

    media_types = {
        "markdown": "text/markdown",
        "csv": "text/csv",
        "plaintext": "text/plain",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "text/plain",
        "gdoc": "application/json",
    }

    return FileResponse(
        path=str(tmp_path),
        media_type=media_types.get(format, "application/octet-stream"),
        filename=f"{art.title}{suffix}",
    )


def _register_imported_artifact(
    db: SecureGraphDB, session: Session, art_id: NodeId,
) -> None:
    """Register an artifact (and its children) imported via raw GraphDB in the security layer."""
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

    # Register parent mappings for all child nodes
    children = db._db.get_children(art_id)
    for child in children:
        resolver.register_parent(child.meta.id, art_id)
        # Also register grandchildren (e.g. Sheet -> Cell)
        grandchildren = db._db.get_children(child.meta.id)
        for gc in grandchildren:
            resolver.register_parent(gc.meta.id, child.meta.id)

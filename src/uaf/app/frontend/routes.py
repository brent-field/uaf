"""HTMX frontend routes — server-rendered pages for the UAF demo."""

from __future__ import annotations

import contextlib
import tempfile
import uuid
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from uaf.app.api.dependencies import get_db, get_registry
from uaf.app.formats import FormatHandler
from uaf.app.formats.csv_format import CsvHandler
from uaf.app.formats.docx_format import DocxHandler
from uaf.app.formats.gdoc_format import GdocHandler
from uaf.app.formats.markdown import MarkdownHandler
from uaf.app.formats.pdf_format import PdfHandler
from uaf.app.formats.plaintext import PlainTextHandler
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.actions import (
    DeleteNode,
    InsertText,
    RenameArtifact,
    ReorderNodes,
    SetCellValue,
)
from uaf.core.errors import AuthenticationError
from uaf.core.node_id import NodeId
from uaf.core.nodes import Artifact, NodeType
from uaf.security.auth import PasswordCredentials, TokenCredentials
from uaf.security.primitives import PrincipalId
from uaf.security.secure_graph_db import SecureGraphDB, Session

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()

_FORMAT_HANDLERS: dict[str, FormatHandler] = {
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_token(request: Request) -> str | None:
    """Extract JWT from cookie."""
    return request.cookies.get("uaf_token")


def _get_session_or_none(
    request: Request, db: SecureGraphDB,
) -> Session | None:
    """Try to authenticate from cookie; return None on failure."""
    token = _get_token(request)
    if not token:
        return None
    try:
        return db.authenticate(TokenCredentials(token=token))
    except (AuthenticationError, Exception):
        return None


def _require_session(request: Request, db: SecureGraphDB) -> Session:
    """Get session or raise 401."""
    session = _get_session_or_none(request, db)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


def _user_ctx(session: Session) -> dict[str, Any]:
    """Build template context dict for the current user."""
    return {
        "display_name": session.principal.display_name,
        "principal_id": session.principal.id.value,
    }


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


def _parse_doc_blocks(
    content: str, db: SecureGraphDB, session: Session, artifact_id: NodeId,
) -> list[dict[str, str]]:
    """Parse DocLens HTML into per-block dicts for the template."""
    children = db.get_children(session, artifact_id)
    blocks: list[dict[str, str]] = []
    for child in children:
        nid = str(child.meta.id)
        html, text, node_type = _render_single_block(child)
        if html:
            blocks.append({"id": nid, "html": html, "text": text, "type": node_type})
    return blocks


def _render_single_block(node: object) -> tuple[str, str, str]:
    """Render one node to (html, raw_text, node_type)."""
    from uaf.core.nodes import CodeBlock, Heading, Image, MathBlock, Paragraph, TextBlock

    match node:
        case Heading(text=text, level=level):
            tag = f"h{min(max(level, 1), 6)}"
            return f"<{tag}>{escape(text)}</{tag}>", text, "heading"
        case Paragraph(text=text, style=style):
            cls = f' class="{escape(style)}"' if style != "body" else ""
            return f"<p{cls}>{escape(text)}</p>", text, "paragraph"
        case CodeBlock(source=source, language=language):
            lang_cls = f' class="language-{escape(language)}"' if language else ""
            return (
                f"<pre><code{lang_cls}>{escape(source)}</code></pre>",
                source, "code_block",
            )
        case MathBlock(source=source, equation_number=eq_num):
            eq_html = (
                f' <span class="eq-number">{escape(eq_num)}</span>'
                if eq_num else ""
            )
            return (
                f'<div class="math-block"><code>{escape(source)}</code>'
                f"{eq_html}</div>",
                source, "math_block",
            )
        case TextBlock(text=text):
            return f'<div class="text-block">{escape(text)}</div>', text, "text_block"
        case Image(uri=uri, alt_text=alt_text):
            return (
                f'<img src="{escape(uri)}" alt="{escape(alt_text)}" />',
                alt_text, "image",
            )
        case _:
            return "", "", ""


# ---------------------------------------------------------------------------
# Auth pages
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    """Show login form."""
    ctx: dict[str, Any] = {"request": request, "mode": "login", "user": None}
    return templates.TemplateResponse("login.html", ctx)


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    """Show register form."""
    ctx: dict[str, Any] = {"request": request, "mode": "register", "user": None}
    return templates.TemplateResponse("login.html", ctx)


@router.post("/login", response_model=None)
def login_submit(
    request: Request,
    principal_id: str = Form(...),
    password: str = Form(...),
    db: SecureGraphDB = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    """Handle login form submission."""
    try:
        session = db.authenticate(
            PasswordCredentials(
                principal_id=PrincipalId(value=principal_id),
                password=password,
            )
        )
    except AuthenticationError:
        ctx: dict[str, Any] = {
            "request": request,
            "mode": "login",
            "user": None,
            "error": "Invalid credentials.",
        }
        return templates.TemplateResponse("login.html", ctx)

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("uaf_token", session.token, httponly=True, samesite="lax")
    return response


@router.post("/register", response_model=None)
def register_submit(
    request: Request,
    display_name: str = Form(...),
    password: str = Form(...),
    db: SecureGraphDB = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    """Handle register form submission."""
    from uaf.security.auth import LocalAuthProvider

    auth = db._auth
    if not isinstance(auth, LocalAuthProvider):
        ctx: dict[str, Any] = {
            "request": request,
            "mode": "register",
            "user": None,
            "error": "Registration not supported.",
        }
        return templates.TemplateResponse("login.html", ctx)

    principal = auth.create_principal(display_name, password)
    session = db.authenticate(
        PasswordCredentials(principal_id=principal.id, password=password)
    )
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("uaf_token", session.token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
def logout() -> RedirectResponse:
    """Clear auth cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("uaf_token")
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_model=None)
def dashboard(
    request: Request,
    db: SecureGraphDB = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Show artifact list."""
    session = _get_session_or_none(request, db)
    if session is None:
        return RedirectResponse(url="/login", status_code=303)

    artifacts = db.find_by_type(session, NodeType.ARTIFACT)
    items: list[dict[str, Any]] = []
    for art in artifacts:
        if isinstance(art, Artifact):
            children = db.get_children(session, art.meta.id)
            items.append({
                "id": str(art.meta.id),
                "title": art.title,
                "child_count": len(children),
                "updated_at": art.meta.updated_at,
            })

    ctx: dict[str, Any] = {
        "request": request,
        "user": _user_ctx(session),
        "artifacts": items,
    }
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/", response_model=None)
def root(request: Request, db: SecureGraphDB = Depends(get_db)) -> RedirectResponse:
    """Redirect root to dashboard or login."""
    session = _get_session_or_none(request, db)
    if session is None:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


# ---------------------------------------------------------------------------
# Artifact CRUD (HTML)
# ---------------------------------------------------------------------------


@router.post("/artifacts/create")
def create_artifact(
    request: Request,
    db: SecureGraphDB = Depends(get_db),
) -> RedirectResponse:
    """Create a new empty document artifact."""
    session = _require_session(request, db)
    from uaf.core.nodes import make_node_metadata

    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Untitled Document")
    art_id = db.create_node(session, art)
    return RedirectResponse(url=f"/artifacts/{art_id}/edit", status_code=303)


@router.delete("/artifacts/{artifact_id}", response_class=HTMLResponse)
def delete_artifact(
    request: Request,
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
) -> HTMLResponse:
    """Delete artifact and return updated list (HTMX partial)."""
    session = _require_session(request, db)
    nid = NodeId(value=uuid.UUID(artifact_id))
    db.delete_node(session, nid)

    # Return updated artifact list as HTML partial
    artifacts = db.find_by_type(session, NodeType.ARTIFACT)
    items: list[dict[str, Any]] = []
    for art in artifacts:
        if isinstance(art, Artifact):
            children = db.get_children(session, art.meta.id)
            items.append({
                "id": str(art.meta.id),
                "title": art.title,
                "child_count": len(children),
                "updated_at": art.meta.updated_at,
            })
    ctx: dict[str, Any] = {"request": request, "artifacts": items}
    return templates.TemplateResponse("partials/artifact_list.html", ctx)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@router.post("/artifacts/import")
def import_artifact(
    request: Request,
    file: UploadFile,
    db: SecureGraphDB = Depends(get_db),
) -> RedirectResponse:
    """Import a file and redirect to the editor."""
    session = _require_session(request, db)

    original_name = file.filename or "upload.txt"
    suffix = Path(original_name).suffix.lower()
    fmt = _EXT_TO_FORMAT.get(suffix)
    if fmt is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    handler = _FORMAT_HANDLERS[fmt]
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
        art_id = handler.import_file(final_path, db._db)
    finally:
        final_path.unlink(missing_ok=True)
        import_dir.rmdir()

    # Register the imported artifact and its children in the security layer
    _register_imported_artifact(db, session, art_id)

    # Route to appropriate editor based on format
    if fmt == "csv":
        return RedirectResponse(url=f"/artifacts/{art_id}/grid", status_code=303)
    return RedirectResponse(url=f"/artifacts/{art_id}/edit", status_code=303)


# ---------------------------------------------------------------------------
# Document editor
# ---------------------------------------------------------------------------


@router.get("/artifacts/{artifact_id}/edit", response_model=None)
def editor_page(
    request: Request,
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse | RedirectResponse:
    """Render the document editor page."""
    session = _get_session_or_none(request, db)
    if session is None:
        return RedirectResponse(url="/login", status_code=303)

    aid = NodeId(value=uuid.UUID(artifact_id))
    art = db.get_node(session, aid)
    if art is None or not isinstance(art, Artifact):
        raise HTTPException(status_code=404, detail="Artifact not found")

    blocks = _parse_doc_blocks("", db, session, aid)

    ctx: dict[str, Any] = {
        "request": request,
        "user": _user_ctx(session),
        "artifact_id": artifact_id,
        "title": art.title,
        "blocks": blocks,
        "view_mode": "semantic",
    }
    return templates.TemplateResponse("editor.html", ctx)


@router.get("/artifacts/{artifact_id}/blocks", response_class=HTMLResponse)
def get_blocks(
    request: Request,
    artifact_id: str,
    mode: str = "semantic",
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Return doc blocks partial in the requested view mode."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))

    if mode == "layout":
        return _render_layout_blocks(request, db, session, aid, artifact_id)

    blocks = _parse_doc_blocks("", db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "blocks": blocks,
    }
    return templates.TemplateResponse("partials/doc_blocks.html", ctx)


def _render_layout_blocks(
    request: Request,
    db: SecureGraphDB,
    session: Session,
    aid: NodeId,
    artifact_id: str,
) -> HTMLResponse:
    """Render layout-view HTML partial."""
    from uaf.app.lenses.doc_lens import DocLens

    lens = DocLens()
    view = lens.render_layout(db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "layout_html": view.content,
    }
    return templates.TemplateResponse("partials/doc_layout.html", ctx)


@router.post("/artifacts/{artifact_id}/rename", response_class=HTMLResponse)
def rename_artifact(
    request: Request,
    artifact_id: str,
    title: str = Form(...),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Rename artifact and return updated doc blocks."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))

    lens = registry.get("doc")
    if lens is not None:
        lens.apply_action(db, session, aid, RenameArtifact(artifact_id=aid, title=title))

    blocks = _parse_doc_blocks("", db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "blocks": blocks,
    }
    return templates.TemplateResponse("partials/doc_blocks.html", ctx)


@router.post("/artifacts/{artifact_id}/action/insert", response_class=HTMLResponse)
def insert_block(
    request: Request,
    artifact_id: str,
    style: str = Form("paragraph"),
    text: str = Form(""),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Insert a new block and return updated doc content."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))

    # Insert at end
    children = db.get_children(session, aid)
    position = len(children)

    lens = registry.get("doc")
    if lens is not None:
        lens.apply_action(
            db, session, aid,
            InsertText(parent_id=aid, text=text, position=position, style=style),
        )

    blocks = _parse_doc_blocks("", db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "blocks": blocks,
    }
    return templates.TemplateResponse("partials/doc_blocks.html", ctx)


@router.post("/artifacts/{artifact_id}/action/delete", response_class=HTMLResponse)
def delete_block(
    request: Request,
    artifact_id: str,
    node_id: str = Form(...),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Delete a block and return updated doc content."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))
    nid = NodeId(value=uuid.UUID(node_id))

    lens = registry.get("doc")
    if lens is not None:
        lens.apply_action(db, session, aid, DeleteNode(node_id=nid))

    blocks = _parse_doc_blocks("", db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "blocks": blocks,
    }
    return templates.TemplateResponse("partials/doc_blocks.html", ctx)


@router.post("/artifacts/{artifact_id}/action/edit", response_class=HTMLResponse)
def edit_block(
    request: Request,
    artifact_id: str,
    node_id: str = Form(...),
    text: str = Form(""),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Update a block's text content and return updated doc content."""
    from uaf.core.nodes import CodeBlock, Heading, Paragraph, TextBlock

    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))
    nid = NodeId(value=uuid.UUID(node_id))

    existing = db.get_node(session, nid)
    if existing is not None:
        match existing:
            case Heading(meta=meta, level=level):
                db.update_node(session, Heading(meta=meta, text=text, level=level))
            case Paragraph(meta=meta, style=style):
                db.update_node(session, Paragraph(meta=meta, text=text, style=style))
            case CodeBlock(meta=meta, language=lang):
                db.update_node(session, CodeBlock(meta=meta, source=text, language=lang))
            case TextBlock(meta=meta):
                db.update_node(session, TextBlock(meta=meta, text=text))

    blocks = _parse_doc_blocks("", db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "blocks": blocks,
    }
    return templates.TemplateResponse("partials/doc_blocks.html", ctx)


@router.post("/artifacts/{artifact_id}/action/move-up", response_class=HTMLResponse)
def move_block_up(
    request: Request,
    artifact_id: str,
    node_id: str = Form(...),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Move a block up one position."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))
    nid = NodeId(value=uuid.UUID(node_id))

    children = db.get_children(session, aid)
    child_ids = [c.meta.id for c in children]
    idx = next((i for i, cid in enumerate(child_ids) if cid == nid), -1)
    if idx > 0:
        child_ids[idx], child_ids[idx - 1] = child_ids[idx - 1], child_ids[idx]
        lens = registry.get("doc")
        if lens is not None:
            lens.apply_action(
                db, session, aid,
                ReorderNodes(parent_id=aid, new_order=tuple(child_ids)),
            )

    blocks = _parse_doc_blocks("", db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "blocks": blocks,
    }
    return templates.TemplateResponse("partials/doc_blocks.html", ctx)


@router.post("/artifacts/{artifact_id}/action/move-down", response_class=HTMLResponse)
def move_block_down(
    request: Request,
    artifact_id: str,
    node_id: str = Form(...),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Move a block down one position."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))
    nid = NodeId(value=uuid.UUID(node_id))

    children = db.get_children(session, aid)
    child_ids = [c.meta.id for c in children]
    idx = next((i for i, cid in enumerate(child_ids) if cid == nid), -1)
    if 0 <= idx < len(child_ids) - 1:
        child_ids[idx], child_ids[idx + 1] = child_ids[idx + 1], child_ids[idx]
        lens = registry.get("doc")
        if lens is not None:
            lens.apply_action(
                db, session, aid,
                ReorderNodes(parent_id=aid, new_order=tuple(child_ids)),
            )

    blocks = _parse_doc_blocks("", db, session, aid)
    ctx: dict[str, Any] = {
        "request": request,
        "artifact_id": artifact_id,
        "blocks": blocks,
    }
    return templates.TemplateResponse("partials/doc_blocks.html", ctx)


# ---------------------------------------------------------------------------
# Spreadsheet viewer/editor
# ---------------------------------------------------------------------------


@router.get("/artifacts/{artifact_id}/grid", response_model=None)
def spreadsheet_page(
    request: Request,
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse | RedirectResponse:
    """Render the spreadsheet viewer."""
    session = _get_session_or_none(request, db)
    if session is None:
        return RedirectResponse(url="/login", status_code=303)

    aid = NodeId(value=uuid.UUID(artifact_id))
    art = db.get_node(session, aid)
    if art is None or not isinstance(art, Artifact):
        raise HTTPException(status_code=404, detail="Artifact not found")

    lens = registry.get("grid")
    grid_html = ""
    sheet_id = ""
    if lens is not None:
        view = lens.render(db, session, aid)
        grid_html = view.content

    # Find first sheet ID for toolbar actions
    from uaf.core.nodes import Sheet

    children = db.get_children(session, aid)
    for child in children:
        if isinstance(child, Sheet):
            sheet_id = str(child.meta.id)
            break

    ctx: dict[str, Any] = {
        "request": request,
        "user": _user_ctx(session),
        "artifact_id": artifact_id,
        "title": art.title,
        "grid_html": grid_html,
        "sheet_id": sheet_id,
    }
    return templates.TemplateResponse("spreadsheet.html", ctx)


@router.post("/artifacts/{artifact_id}/grid/rename", response_class=HTMLResponse)
def rename_grid_artifact(
    request: Request,
    artifact_id: str,
    title: str = Form(...),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Rename spreadsheet artifact and return updated grid."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))

    lens = registry.get("grid")
    if lens is not None:
        lens.apply_action(db, session, aid, RenameArtifact(artifact_id=aid, title=title))
        view = lens.render(db, session, aid)
        grid_html = view.content
    else:
        grid_html = ""

    ctx: dict[str, Any] = {
        "request": request,
        "grid_html": grid_html,
    }
    return templates.TemplateResponse("partials/grid_table.html", ctx)


@router.post("/artifacts/{artifact_id}/grid/set-cell", response_class=HTMLResponse)
def set_cell_value(
    request: Request,
    artifact_id: str,
    cell_id: str = Form(...),
    value: str = Form(""),
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Set a cell's value and return updated grid."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))
    cid = NodeId(value=uuid.UUID(cell_id))

    lens = registry.get("grid")
    if lens is not None:
        # Try to parse as number
        cell_value: str | int | float = value
        try:
            cell_value = int(value)
        except ValueError:
            with contextlib.suppress(ValueError):
                cell_value = float(value)

        lens.apply_action(db, session, aid, SetCellValue(cell_id=cid, value=cell_value))
        view = lens.render(db, session, aid)
        grid_html = view.content
    else:
        grid_html = ""

    ctx: dict[str, Any] = {
        "request": request,
        "grid_html": grid_html,
    }
    return templates.TemplateResponse("partials/grid_table.html", ctx)


@router.post("/artifacts/{artifact_id}/grid/add-row", response_class=HTMLResponse)
def add_row(
    request: Request,
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Add a row to the spreadsheet."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))

    from uaf.app.lenses.actions import InsertRow
    from uaf.core.nodes import Sheet

    children = db.get_children(session, aid)
    sheet_id = None
    rows = 0
    for child in children:
        if isinstance(child, Sheet):
            sheet_id = child.meta.id
            rows = child.rows
            break

    lens = registry.get("grid")
    if lens is not None and sheet_id is not None:
        lens.apply_action(db, session, aid, InsertRow(sheet_id=sheet_id, position=rows))
        view = lens.render(db, session, aid)
        grid_html = view.content
    else:
        grid_html = ""

    ctx: dict[str, Any] = {"request": request, "grid_html": grid_html}
    return templates.TemplateResponse("partials/grid_table.html", ctx)


@router.post("/artifacts/{artifact_id}/grid/add-col", response_class=HTMLResponse)
def add_col(
    request: Request,
    artifact_id: str,
    db: SecureGraphDB = Depends(get_db),
    registry: LensRegistry = Depends(get_registry),
) -> HTMLResponse:
    """Add a column to the spreadsheet."""
    session = _require_session(request, db)
    aid = NodeId(value=uuid.UUID(artifact_id))

    from uaf.app.lenses.actions import InsertColumn
    from uaf.core.nodes import Sheet

    children = db.get_children(session, aid)
    sheet_id = None
    cols = 0
    for child in children:
        if isinstance(child, Sheet):
            sheet_id = child.meta.id
            cols = child.cols
            break

    lens = registry.get("grid")
    if lens is not None and sheet_id is not None:
        lens.apply_action(db, session, aid, InsertColumn(sheet_id=sheet_id, position=cols))
        view = lens.render(db, session, aid)
        grid_html = view.content
    else:
        grid_html = ""

    ctx: dict[str, Any] = {"request": request, "grid_html": grid_html}
    return templates.TemplateResponse("partials/grid_table.html", ctx)

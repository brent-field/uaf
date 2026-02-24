"""DocLens — document rendering and editing lens."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from uaf.app.lenses import LensView
from uaf.app.lenses.actions import (
    DeleteNode,
    DeleteText,
    FormatText,
    InsertText,
    MoveNode,
    RenameArtifact,
    ReorderNodes,
)
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, utc_now
from uaf.core.nodes import (
    Artifact,
    CodeBlock,
    Heading,
    Image,
    LayoutHint,
    NodeType,
    Paragraph,
    RawNode,
    TextBlock,
    make_node_metadata,
)
from uaf.core.operations import ReorderChildren

if TYPE_CHECKING:
    from uaf.app.lenses.actions import LensAction
    from uaf.core.node_id import NodeId
    from uaf.security.secure_graph_db import SecureGraphDB, Session

_SUPPORTED = frozenset({
    NodeType.ARTIFACT,
    NodeType.PARAGRAPH,
    NodeType.HEADING,
    NodeType.TEXT_BLOCK,
    NodeType.CODE_BLOCK,
    NodeType.IMAGE,
})


class DocLens:
    """Renders a document artifact as HTML and translates editing actions."""

    @property
    def lens_type(self) -> str:
        return "doc"

    @property
    def supported_node_types(self) -> frozenset[NodeType]:
        return _SUPPORTED

    def render(
        self, db: SecureGraphDB, session: Session, artifact_id: NodeId
    ) -> LensView:
        """Render the artifact tree as semantic HTML."""
        artifact = db.get_node(session, artifact_id)
        if artifact is None or not isinstance(artifact, Artifact):
            return LensView(
                lens_type="doc",
                artifact_id=artifact_id,
                title="(not found)",
                content="",
                content_type="text/html",
                node_count=0,
                rendered_at=utc_now(),
            )

        children = db.get_children(session, artifact_id)
        parts: list[str] = []
        node_count = 1  # count the artifact itself

        for child in children:
            html, count = self._render_node(child, db, session)
            parts.append(html)
            node_count += count

        inner = "\n".join(parts)
        content = (
            f'<article data-artifact-id="{artifact_id}">\n'
            f"  <h1 data-node-id=\"{artifact_id}\">{escape(artifact.title)}</h1>\n"
            f"{inner}\n"
            f"</article>"
        )

        return LensView(
            lens_type="doc",
            artifact_id=artifact_id,
            title=artifact.title,
            content=content,
            content_type="text/html",
            node_count=node_count,
            rendered_at=utc_now(),
        )

    def render_layout(
        self, db: SecureGraphDB, session: Session, artifact_id: NodeId
    ) -> LensView:
        """Render the artifact tree as layout-positioned HTML."""
        artifact = db.get_node(session, artifact_id)
        if artifact is None or not isinstance(artifact, Artifact):
            return LensView(
                lens_type="doc",
                artifact_id=artifact_id,
                title="(not found)",
                content="",
                content_type="text/html",
                node_count=0,
                rendered_at=utc_now(),
            )

        # Page dimensions from artifact layout (default US Letter).
        page_w, page_h = _page_dimensions(artifact)
        children = db.get_children(session, artifact_id)

        # Group children by page number.
        pages: dict[int, list[object]] = {}
        flow_nodes: list[object] = []  # nodes without coordinates
        node_count = 1

        for child in children:
            layout = _get_layout(child)
            if layout is not None and layout.x is not None and layout.y is not None:
                pg = layout.page if layout.page is not None else 0
                pages.setdefault(pg, []).append(child)
            else:
                flow_nodes.append(child)
            node_count += 1

        parts: list[str] = []

        if pages:
            for pg_num in sorted(pages):
                page_parts: list[str] = []
                for node in pages[pg_num]:
                    page_parts.append(self._render_layout_node(node))
                inner = "\n".join(page_parts)
                parts.append(
                    f'<div class="layout-page" style="position: relative;'
                    f" width: {page_w}pt; height: {page_h}pt;"
                    f' margin: 0 auto 1rem; background: #fff;'
                    f' border: 1px solid #ccc;">\n{inner}\n</div>'
                )

        if flow_nodes:
            flow_parts: list[str] = []
            for node in flow_nodes:
                flow_parts.append(self._render_layout_flow_node(node))
            flow_inner = "\n".join(flow_parts)
            parts.append(
                f'<div class="layout-flow"'
                f' style="max-width: {page_w}pt;'
                f' margin: 0 auto; padding: 1rem;">'
                f"\n{flow_inner}\n</div>"
            )

        if not parts:
            parts.append(
                '<div class="empty-state">'
                "<p>No layout data available."
                " Import a PDF or DOCX to see layout view.</p></div>"
            )

        content = "\n".join(parts)
        return LensView(
            lens_type="doc",
            artifact_id=artifact_id,
            title=artifact.title,
            content=content,
            content_type="text/html",
            node_count=node_count,
            rendered_at=utc_now(),
        )

    def apply_action(
        self,
        db: SecureGraphDB,
        session: Session,
        artifact_id: NodeId,
        action: LensAction,
    ) -> None:
        """Translate a LensAction into graph operations."""
        match action:
            case InsertText(parent_id=parent_id, text=text, position=pos, style=style):
                self._insert_text(db, session, parent_id, text, pos, style)
            case DeleteText(node_id=node_id):
                self._delete_text(db, session, node_id)
            case FormatText(node_id=node_id, style=style, level=level):
                self._format_text(db, session, node_id, style, level)
            case ReorderNodes(parent_id=parent_id, new_order=new_order):
                self._reorder(db, session, parent_id, new_order)
            case DeleteNode(node_id=node_id):
                self._delete_text(db, session, node_id)
            case RenameArtifact(artifact_id=aid, title=title):
                self._rename(db, session, aid, title)
            case MoveNode(node_id=nid, new_parent_id=new_parent):
                self._move(db, session, nid, new_parent)
            case _:
                msg = f"DocLens does not support action: {type(action).__name__}"
                raise ValueError(msg)

    # ------------------------------------------------------------------
    # Layout rendering helpers
    # ------------------------------------------------------------------

    def _render_layout_node(self, node: object) -> str:
        """Render a node as an absolutely-positioned div."""
        layout = _get_layout(node)
        text = _get_text(node)
        nid = _get_node_id(node)

        if layout is None or text is None or nid is None:
            return ""

        style_parts = ["position: absolute"]
        if layout.x is not None:
            style_parts.append(f"left: {layout.x}pt")
        if layout.y is not None:
            style_parts.append(f"top: {layout.y}pt")
        if layout.width is not None:
            style_parts.append(f"width: {layout.width}pt")
        # No explicit height — let content flow naturally to avoid
        # clipping when HTML font metrics differ from the PDF engine.
        if layout.reading_order is not None:
            style_parts.append(f"z-index: {1000 - layout.reading_order}")
        if layout.rotation is not None:
            style_parts.append(f"transform: rotate({layout.rotation}deg)")
            style_parts.append("transform-origin: top left")
        style_parts.extend(_font_style_parts(layout))

        css_class = "layout-block"
        if layout.header_footer:
            css_class += " layout-header-footer"

        style = "; ".join(style_parts)
        # Preserve line breaks from PDF extraction, with per-line bold
        # when the first line has a different weight from the block.
        escaped = _format_layout_text(text, layout)
        return (
            f'  <div data-node-id="{nid}" class="{css_class}"'
            f' style="{style}">{escaped}</div>'
        )

    def _render_layout_flow_node(self, node: object) -> str:
        """Render a node in flow layout (no absolute positioning)."""
        layout = _get_layout(node)
        text = _get_text(node)
        nid = _get_node_id(node)

        if text is None or nid is None:
            return ""

        style_parts = _font_style_parts(layout) if layout else []
        style_attr = f' style="{"; ".join(style_parts)}"' if style_parts else ""

        return (
            f'  <div data-node-id="{nid}" class="layout-block"'
            f"{style_attr}>{escape(text)}</div>"
        )

    # ------------------------------------------------------------------
    # Semantic rendering helpers
    # ------------------------------------------------------------------

    def _render_node(
        self, node: object, db: SecureGraphDB, session: Session
    ) -> tuple[str, int]:
        """Render a single node to HTML. Returns (html, node_count)."""
        match node:
            case Heading(meta=meta, text=text, level=level):
                tag = f"h{min(max(level, 1), 6)}"
                return (
                    f'  <{tag} data-node-id="{meta.id}">'
                    f"{escape(text)}</{tag}>",
                    1,
                )
            case Paragraph(meta=meta, text=text, style=style):
                cls = f' class="{escape(style)}"' if style != "body" else ""
                return (
                    f'  <p data-node-id="{meta.id}"{cls}>'
                    f"{escape(text)}</p>",
                    1,
                )
            case CodeBlock(meta=meta, source=source, language=language):
                lang_attr = (
                    f' class="language-{escape(language)}"' if language else ""
                )
                return (
                    f'  <pre data-node-id="{meta.id}">'
                    f"<code{lang_attr}>{escape(source)}</code></pre>",
                    1,
                )
            case TextBlock(meta=meta, text=text):
                return (
                    f'  <div data-node-id="{meta.id}" class="text-block">'
                    f"{escape(text)}</div>",
                    1,
                )
            case Image(meta=meta, uri=uri, alt_text=alt_text):
                return (
                    f'  <img data-node-id="{meta.id}" '
                    f'src="{escape(uri)}" alt="{escape(alt_text)}" />',
                    1,
                )
            case RawNode(meta=meta, original_type=ot):
                return (
                    f'  <div data-node-id="{meta.id}" class="raw-node">'
                    f"[unknown type: {escape(ot)}]</div>",
                    1,
                )
            case _:
                return ("", 0)

    # ------------------------------------------------------------------
    # Action helpers
    # ------------------------------------------------------------------

    def _insert_text(
        self,
        db: SecureGraphDB,
        session: Session,
        parent_id: NodeId,
        text: str,
        position: int,
        style: str,
    ) -> None:
        """Insert a text node as a child of parent_id."""
        new_node: Heading | CodeBlock | Paragraph
        if style == "heading":
            new_node = Heading(
                meta=make_node_metadata(NodeType.HEADING), text=text, level=1,
            )
        elif style == "code_block":
            new_node = CodeBlock(
                meta=make_node_metadata(NodeType.CODE_BLOCK), source=text, language="",
            )
        else:
            new_node = Paragraph(
                meta=make_node_metadata(NodeType.PARAGRAPH), text=text,
            )

        node_id = db.create_node(session, new_node)
        edge = Edge(
            id=EdgeId.generate(),
            source=parent_id,
            target=node_id,
            edge_type=EdgeType.CONTAINS,
            created_at=utc_now(),
        )
        db.create_edge(session, edge)

        # Reorder to place at requested position
        children = db.get_children(session, parent_id)
        child_ids = [c.meta.id for c in children]
        # node_id is already at the end from create_edge; move it to position
        if node_id in child_ids:
            child_ids.remove(node_id)
        child_ids.insert(min(position, len(child_ids)), node_id)
        op = ReorderChildren(
            parent_id=parent_id,
            new_order=tuple(child_ids),
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        db._db.apply(op)

    def _delete_text(
        self, db: SecureGraphDB, session: Session, node_id: NodeId
    ) -> None:
        """Delete a text node and its CONTAINS edge."""
        state = db._db._materializer.state
        for eid, edge in list(state.edges.items()):
            if edge.target == node_id and edge.edge_type == EdgeType.CONTAINS:
                db.delete_edge(session, eid)
        db.delete_node(session, node_id)

    def _format_text(
        self,
        db: SecureGraphDB,
        session: Session,
        node_id: NodeId,
        style: str,
        level: int,
    ) -> None:
        """Change a node's format/type."""
        existing = db.get_node(session, node_id)
        if existing is None:
            return

        # Extract text from existing node
        text = ""
        match existing:
            case Heading(text=t):
                text = t
            case Paragraph(text=t):
                text = t
            case CodeBlock(source=s):
                text = s
            case TextBlock(text=t):
                text = t
            case _:
                return

        meta = existing.meta

        # Build replacement node with same ID and metadata
        if style == "heading":
            new_node: object = Heading(meta=meta, text=text, level=level)
        elif style == "code_block":
            new_node = CodeBlock(meta=meta, source=text, language="")
        else:
            new_node = Paragraph(meta=meta, text=text)

        db.update_node(session, new_node)

    def _reorder(
        self,
        db: SecureGraphDB,
        session: Session,
        parent_id: NodeId,
        new_order: tuple[NodeId, ...],
    ) -> None:
        """Reorder children of a parent node."""
        op = ReorderChildren(
            parent_id=parent_id,
            new_order=new_order,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        db._db.apply(op)

    def _rename(
        self, db: SecureGraphDB, session: Session, artifact_id: NodeId, title: str
    ) -> None:
        """Rename an artifact."""
        artifact = db.get_node(session, artifact_id)
        if artifact is None or not isinstance(artifact, Artifact):
            return
        updated = Artifact(meta=artifact.meta, title=title)
        db.update_node(session, updated)

    def _move(
        self,
        db: SecureGraphDB,
        session: Session,
        node_id: NodeId,
        new_parent_id: NodeId,
    ) -> None:
        """Move a node to a new parent."""
        from uaf.core.operations import MoveNode as MoveNodeOp

        op = MoveNodeOp(
            node_id=node_id,
            new_parent_id=new_parent_id,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id=session.principal.id.value,
        )
        db._db.apply(op)


# ---------------------------------------------------------------------------
# Module-level helpers for layout rendering
# ---------------------------------------------------------------------------

# Default US Letter dimensions in points.
_DEFAULT_PAGE_W = 612.0
_DEFAULT_PAGE_H = 792.0


def _page_dimensions(artifact: Artifact) -> tuple[float, float]:
    """Extract page width/height from artifact layout or use defaults."""
    layout = artifact.meta.layout
    if layout is not None:
        w = layout.width if layout.width is not None else _DEFAULT_PAGE_W
        h = layout.height if layout.height is not None else _DEFAULT_PAGE_H
        return w, h
    return _DEFAULT_PAGE_W, _DEFAULT_PAGE_H


def _get_layout(node: object) -> LayoutHint | None:
    """Safely extract LayoutHint from any node."""
    if hasattr(node, "meta") and hasattr(node.meta, "layout"):
        return node.meta.layout  # type: ignore[no-any-return]
    return None


def _get_text(node: object) -> str | None:
    """Extract text content from a node."""
    match node:
        case Heading(text=t):
            return t
        case Paragraph(text=t):
            return t
        case CodeBlock(source=s):
            return s
        case TextBlock(text=t):
            return t
        case Image(alt_text=alt):
            return alt
        case _:
            return None


def _get_node_id(node: object) -> object | None:
    """Extract node ID from any node."""
    match node:
        case Heading(meta=meta):
            return meta.id
        case Paragraph(meta=meta):
            return meta.id
        case CodeBlock(meta=meta):
            return meta.id
        case TextBlock(meta=meta):
            return meta.id
        case Image(meta=meta):
            return meta.id
        case RawNode(meta=meta):
            return meta.id
        case _:
            return None


def _font_style_parts(layout: LayoutHint) -> list[str]:
    """Build CSS style parts from LayoutHint font properties."""
    parts: list[str] = []
    if layout.font_family:
        # Font family may contain commas (CSS font stack) — don't escape.
        parts.append(f"font-family: {layout.font_family}")
    if layout.font_size is not None:
        parts.append(f"font-size: {layout.font_size}pt")
    if layout.font_weight:
        parts.append(f"font-weight: {escape(layout.font_weight)}")
    if layout.font_style:
        parts.append(f"font-style: {escape(layout.font_style)}")
    if layout.color:
        parts.append(f"color: {escape(layout.color)}")
    return parts


def _format_layout_text(text: str, layout: LayoutHint) -> str:
    """Format node text as HTML for layout view.

    Handles two concerns:
    - ``\\n`` → ``<br>`` for line breaks
    - First-line bold: if ``layout.first_line_weight`` differs from the
      block's ``font_weight``, the first line is wrapped in a ``<span>``
      with the first-line weight (e.g. bold author name above normal-weight
      affiliation text).
    """
    flw = layout.first_line_weight
    block_w = layout.font_weight or "normal"
    if flw and flw != block_w:
        # Mixed-weight block — bold just the first line.
        if "\n" in text:
            idx = text.index("\n")
            first = escape(text[:idx])
            rest = escape(text[idx + 1:]).replace("\n", "<br>")
            return (
                f'<span style="font-weight: {escape(flw)}">'
                f"{first}</span><br>{rest}"
            )
        return (
            f'<span style="font-weight: {escape(flw)}">'
            f"{escape(text)}</span>"
        )
    return escape(text).replace("\n", "<br>")

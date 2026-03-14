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
    FontAnnotation,
    Heading,
    Image,
    LayoutHint,
    MathBlock,
    NodeType,
    Paragraph,
    RawNode,
    Shape,
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
    NodeType.MATH_BLOCK,
    NodeType.IMAGE,
    NodeType.SHAPE,
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
        aid_str = str(artifact_id.value)
        principal_id = session.principal.id.value
        with db._db.action_group(principal_id, aid_str):
            match action:
                case InsertText(
                    parent_id=parent_id, text=text, position=pos, style=style,
                ):
                    self._insert_text(
                        db, session, parent_id, text, pos, style, aid_str,
                    )
                case DeleteText(node_id=node_id):
                    self._delete_text(db, session, node_id, aid_str)
                case FormatText(node_id=node_id, style=style, level=level):
                    self._format_text(db, session, node_id, style, level)
                case ReorderNodes(parent_id=parent_id, new_order=new_order):
                    self._reorder(db, session, parent_id, new_order)
                case DeleteNode(node_id=node_id):
                    self._delete_text(db, session, node_id, aid_str)
                case RenameArtifact(artifact_id=aid, title=title):
                    self._rename(db, session, aid, title)
                case MoveNode(node_id=nid, new_parent_id=new_parent):
                    self._move(db, session, nid, new_parent)
                case _:
                    msg = (
                        f"DocLens does not support action:"
                        f" {type(action).__name__}"
                    )
                    raise ValueError(msg)

    # ------------------------------------------------------------------
    # Layout rendering helpers
    # ------------------------------------------------------------------

    def _render_layout_node(self, node: object) -> str:
        """Render a node as an absolutely-positioned div."""
        # Shape nodes have no text — handle separately.
        if isinstance(node, Shape):
            return _render_layout_shape(node)

        layout = _get_layout(node)
        text = _get_text(node)
        nid = _get_node_id(node)

        if layout is None or text is None or nid is None:
            return ""

        style_parts = ["position: absolute", "white-space: nowrap"]
        if layout.x is not None:
            style_parts.append(f"left: {layout.x}pt")
        if layout.y is not None:
            style_parts.append(f"top: {layout.y}pt")
        if layout.width is not None:
            style_parts.append(f"width: {layout.width}pt")
        # When child elements use absolute positioning the parent
        # collapses to zero height — set explicit height from the PDF bbox.
        has_abs_children = bool(layout.spans) or bool(layout.line_baselines)
        if has_abs_children and layout.height is not None:
            style_parts.append(f"height: {layout.height}pt")
        if layout.reading_order is not None:
            style_parts.append(f"z-index: {1000 - layout.reading_order}")
        if layout.rotation is not None:
            style_parts.append(f"transform: rotate({layout.rotation}deg)")
            style_parts.append("transform-origin: top left")
        # Skip line-height when per-line positioning handles spacing.
        style_parts.extend(_font_style_parts(
            layout, skip_line_height=has_abs_children,
        ))

        css_class = "layout-block"
        if layout.header_footer:
            css_class += " layout-header-footer"

        # Build data attributes for the inspector.
        data_parts = [f'data-node-id="{nid}"']
        data_parts.append(f'data-node-type="{_get_node_type_name(node)}"')
        if layout.page is not None:
            data_parts.append(f'data-page="{layout.page}"')
        if layout.reading_order is not None:
            data_parts.append(f'data-reading-order="{layout.reading_order}"')
        if layout.height is not None:
            data_parts.append(f'data-height="{layout.height}"')
        if layout.rotation is not None:
            data_parts.append(f'data-rotation="{layout.rotation}"')
        if layout.first_line_weight:
            data_parts.append(
                f'data-first-line-weight="{escape(layout.first_line_weight)}"'
            )
        data_attr_str = " ".join(data_parts)

        style = "; ".join(style_parts)
        # Four rendering paths:
        # 1. Display equations (spans) → absolute positioning per glyph
        # 2. Per-line baselines → each line absolutely positioned
        # 3. Inline math (font_annotations) → normal text flow with
        #    <span> wrappers for math-font character ranges
        # 4. Plain text → escaped text with line breaks
        if layout.spans:
            escaped = _format_spans(layout)
        else:
            render_text = layout.display_text if layout.display_text else text
            if layout.line_baselines:
                if layout.font_annotations:
                    escaped = _format_per_line_annotated_text(
                        render_text, layout.font_annotations,
                        layout.line_baselines, layout,
                    )
                else:
                    escaped = _format_per_line_text(
                        render_text, layout.line_baselines, layout,
                    )
            elif layout.font_annotations:
                escaped = _format_annotated_text(
                    render_text, layout.font_annotations, layout,
                )
            else:
                escaped = _format_layout_text(render_text, layout)
        return (
            f'  <div {data_attr_str} class="{css_class}"'
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
            case MathBlock(meta=meta, source=source, equation_number=eq_num):
                eq_html = (
                    f' <span class="eq-number">{escape(eq_num)}</span>'
                    if eq_num else ""
                )
                return (
                    f'  <div data-node-id="{meta.id}" class="math-block">'
                    f"<code>{escape(source)}</code>{eq_html}</div>",
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
        artifact_id: str,
    ) -> None:
        """Insert a text node as a child of parent_id."""
        with db._db.action_group(session.principal.id.value, artifact_id):
            new_node: Heading | CodeBlock | Paragraph
            if style == "heading":
                new_node = Heading(
                    meta=make_node_metadata(NodeType.HEADING), text=text, level=1,
                )
            elif style == "code_block":
                new_node = CodeBlock(
                    meta=make_node_metadata(NodeType.CODE_BLOCK),
                    source=text, language="",
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
        self, db: SecureGraphDB, session: Session, node_id: NodeId,
        artifact_id: str,
    ) -> None:
        """Delete a text node and its CONTAINS edge."""
        with db._db.action_group(session.principal.id.value, artifact_id):
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
        case MathBlock(source=s):
            return s
        case TextBlock(text=t):
            return t
        case Image(alt_text=alt):
            return alt
        case _:
            return None


def _get_node_type_name(node: object) -> str:
    """Return a human-readable node type name for inspector data attributes."""
    match node:
        case Heading():
            return "heading"
        case Paragraph():
            return "paragraph"
        case CodeBlock():
            return "code_block"
        case MathBlock():
            return "math_block"
        case TextBlock():
            return "text_block"
        case Image():
            return "image"
        case Shape():
            return "shape"
        case _:
            return "unknown"


def _get_node_id(node: object) -> object | None:
    """Extract node ID from any node."""
    match node:
        case Heading(meta=meta):
            return meta.id
        case Paragraph(meta=meta):
            return meta.id
        case CodeBlock(meta=meta):
            return meta.id
        case MathBlock(meta=meta):
            return meta.id
        case TextBlock(meta=meta):
            return meta.id
        case Image(meta=meta):
            return meta.id
        case Shape(meta=meta):
            return meta.id
        case RawNode(meta=meta):
            return meta.id
        case _:
            return None


def _render_layout_shape(node: Shape) -> str:
    """Render a Shape node as an absolutely-positioned div."""
    layout = node.meta.layout
    nid = node.meta.id

    style_parts = ["position: absolute", "pointer-events: none"]
    if layout is not None:
        if layout.x is not None:
            style_parts.append(f"left: {layout.x}pt")
        if layout.y is not None:
            style_parts.append(f"top: {layout.y}pt")
        if layout.width is not None:
            style_parts.append(f"width: {layout.width}pt")
        if layout.height is not None:
            style_parts.append(f"height: {layout.height}pt")
        if layout.reading_order is not None:
            style_parts.append(f"z-index: {1000 - layout.reading_order}")

    # Use fill color from layout, default to black.
    color = (layout.color if layout is not None and layout.color else "#000000")
    style_parts.append(f"background: {color}")

    css_class = f"layout-shape layout-shape-{escape(node.shape_type)}"

    data_parts = [
        f'data-node-id="{nid}"',
        'data-node-type="shape"',
        f'data-shape-type="{escape(node.shape_type)}"',
    ]
    if layout is not None and layout.page is not None:
        data_parts.append(f'data-page="{layout.page}"')
    data_attr_str = " ".join(data_parts)

    style = "; ".join(style_parts)
    return f'  <div {data_attr_str} class="{css_class}" style="{style}"></div>'


def _font_style_parts(
    layout: LayoutHint,
    *,
    skip_line_height: bool = False,
) -> list[str]:
    """Build CSS style parts from LayoutHint font properties."""
    parts: list[str] = []
    if layout.font_family:
        # Font family may contain commas (CSS font stack) — don't HTML-escape.
        # However, double-quotes in font names (e.g. "Times New Roman") must
        # be converted to single quotes so they don't break style="..." attrs.
        safe_family = layout.font_family.replace('"', "'")
        parts.append(f"font-family: {safe_family}")
    if layout.font_size is not None:
        parts.append(f"font-size: {layout.font_size}pt")
    if layout.line_height is not None and not skip_line_height:
        parts.append(f"line-height: {layout.line_height}pt")
    if layout.font_weight:
        parts.append(f"font-weight: {escape(layout.font_weight)}")
    if layout.font_style:
        parts.append(f"font-style: {escape(layout.font_style)}")
    if layout.color:
        parts.append(f"color: {escape(layout.color)}")
    return parts


def _format_per_line_text(
    text: str,
    baselines: tuple[float, ...],
    layout: LayoutHint,
) -> str:
    """Format text with per-line absolute positioning.

    Each visual line is wrapped in a ``<span>`` with ``position: absolute``
    and ``top: {baseline}pt``, giving exact control over each line's
    vertical placement.  When ``line_lefts`` is available, a ``left: Xpt``
    offset is also applied (e.g. for centered equations within a paragraph
    block).
    """
    raw_lines = text.split("\n")
    line_lefts = layout.line_lefts

    # First-line bold handling.
    flw = layout.first_line_weight
    block_w = layout.font_weight or "normal"

    parts: list[str] = []
    for i, raw_line in enumerate(raw_lines):
        top = baselines[i] if i < len(baselines) else baselines[-1]
        left = line_lefts[i] if line_lefts and i < len(line_lefts) else None
        escaped_line = escape(raw_line)

        # Apply first-line bold if needed.
        if i == 0 and flw and flw != block_w:
            escaped_line = (
                f'<span style="font-weight: {escape(flw)}">'
                f"{escaped_line}</span>"
            )

        css = "display: block; position: absolute; white-space: nowrap"
        css += f"; top: {top}pt"
        if left is not None and left > 0.5:
            css += f"; left: {left}pt"

        parts.append(
            f'<span class="layout-line" style="{css}">'
            f"{escaped_line}</span>"
        )
    return "".join(parts)


def _format_per_line_annotated_text(
    text: str,
    annotations: tuple[FontAnnotation, ...],
    baselines: tuple[float, ...],
    layout: LayoutHint,
) -> str:
    """Format annotated text with per-line absolute positioning.

    Combines font annotations (inline math styling) with per-line
    baseline positioning.  Each visual line is positioned at its exact
    PDF y-offset and x-offset, and annotations within that line are
    rendered as inline ``<span>`` elements.
    """
    raw_lines = text.split("\n")
    sorted_anns = sorted(annotations, key=lambda a: a.start)
    line_lefts = layout.line_lefts

    flw = layout.first_line_weight
    block_w = layout.font_weight or "normal"

    parts: list[str] = []
    # Track cumulative character offset through the unsplit text.
    line_start = 0
    for i, raw_line in enumerate(raw_lines):
        line_end = line_start + len(raw_line)
        top = baselines[i] if i < len(baselines) else baselines[-1]
        left = line_lefts[i] if line_lefts and i < len(line_lefts) else None

        # Collect annotations that overlap this line's character range.
        line_parts: list[str] = []
        pos = line_start
        for ann in sorted_anns:
            if ann.end <= line_start or ann.start >= line_end:
                continue  # annotation doesn't overlap this line
            a_start = max(ann.start, line_start)
            a_end = min(ann.end, line_end)
            # Emit text before this annotation (within this line).
            if a_start > pos:
                line_parts.append(escape(text[pos:a_start]))
            # Emit the annotated range.
            css: list[str] = []
            safe_family = ann.font_family.replace('"', "'")
            css.append(f"font-family: {safe_family}")
            if ann.font_style:
                css.append(f"font-style: {escape(ann.font_style)}")
            if ann.font_size is not None:
                css.append(f"font-size: {ann.font_size}pt")
            if ann.font_weight:
                css.append(f"font-weight: {escape(ann.font_weight)}")
            if ann.vertical_align is not None:
                css_offset = -ann.vertical_align
                css.append(f"vertical-align: {css_offset}pt")
            style = "; ".join(css)
            line_parts.append(
                f'<span style="{style}">'
                f"{escape(text[a_start:a_end])}</span>"
            )
            pos = a_end

        # Remaining unannotated text on this line.
        if pos < line_end:
            line_parts.append(escape(text[pos:line_end]))

        line_html = "".join(line_parts)

        # Apply first-line bold if needed.
        if i == 0 and flw and flw != block_w:
            line_html = (
                f'<span style="font-weight: {escape(flw)}">'
                f"{line_html}</span>"
            )

        line_css = "display: block; position: absolute; white-space: nowrap"
        line_css += f"; top: {top}pt"
        if left is not None and left > 0.5:
            line_css += f"; left: {left}pt"

        parts.append(
            f'<span class="layout-line" style="{line_css}">'
            f"{line_html}</span>"
        )
        # +1 for the \n separator.
        line_start = line_end + 1

    return "".join(parts)


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


def _format_annotated_text(
    text: str,
    annotations: tuple[FontAnnotation, ...],
    layout: LayoutHint,
) -> str:
    """Format text with inline ``<span>`` wrappers for font annotations.

    Preserves the same text flow as :func:`_format_layout_text` (line
    breaks, first-line bold) but wraps annotated character ranges in
    ``<span style="font-family: ...">`` elements so math characters
    render with the correct glyphs.

    Annotations are character ranges in the *unescaped* ``text``.
    """
    # Build the annotated string by walking through the text and
    # inserting <span> wrappers at annotation boundaries.
    # Sort annotations by start position.
    sorted_anns = sorted(annotations, key=lambda a: a.start)

    parts: list[str] = []
    pos = 0
    for ann in sorted_anns:
        # Emit text before this annotation.
        if ann.start > pos:
            parts.append(escape(text[pos:ann.start]))
        # Emit the annotated range.
        css: list[str] = []
        safe_family = ann.font_family.replace('"', "'")
        css.append(f"font-family: {safe_family}")
        if ann.font_style:
            css.append(f"font-style: {escape(ann.font_style)}")
        if ann.font_size is not None:
            css.append(f"font-size: {ann.font_size}pt")
        if ann.font_weight:
            css.append(f"font-weight: {escape(ann.font_weight)}")
        if ann.vertical_align is not None:
            # Negate: CSS positive = up, PDF positive = down.
            css_offset = -ann.vertical_align
            css.append(f"vertical-align: {css_offset}pt")
        style = "; ".join(css)
        parts.append(
            f'<span style="{style}">'
            f"{escape(text[ann.start:ann.end])}</span>"
        )
        pos = ann.end

    # Emit remaining text after last annotation.
    if pos < len(text):
        parts.append(escape(text[pos:]))

    result = "".join(parts)

    # Apply the same line-break and first-line-bold logic as
    # _format_layout_text, but on the already-annotated HTML.
    # Line breaks: \n was escaped to \n by escape() — no, escape()
    # doesn't escape \n.  The raw \n characters are still in the
    # output from escape().
    result = result.replace("\n", "<br>")

    # First-line bold: wrap content before first <br> in a bold span.
    flw = layout.first_line_weight
    block_w = layout.font_weight or "normal"
    if flw and flw != block_w:
        br_pos = result.find("<br>")
        if br_pos >= 0:
            first = result[:br_pos]
            rest = result[br_pos:]  # includes the <br>
            result = (
                f'<span style="font-weight: {escape(flw)}">'
                f"{first}</span>{rest}"
            )
        else:
            result = (
                f'<span style="font-weight: {escape(flw)}">'
                f"{result}</span>"
            )

    return result


def _format_spans(layout: LayoutHint) -> str:
    """Render per-span HTML with absolute positioning within the block.

    Each span gets ``position: absolute`` with ``left`` / ``top`` from its
    ``x_offset`` / ``y_offset``, enabling overlapping sub/superscripts and
    right-margin equation numbers.

    Per-span ``font-family`` is emitted so that math symbols (e.g. CMSY10
    mapped to Symbol) render with correct glyphs instead of inheriting the
    block's dominant font family.
    """
    assert layout.spans is not None
    block_size = layout.font_size
    block_family = layout.font_family

    parts: list[str] = []
    for span in layout.spans:
        css: list[str] = ["position: absolute"]
        if span.x_offset is not None:
            css.append(f"left: {span.x_offset}pt")
        if span.y_offset is not None:
            css.append(f"top: {span.y_offset}pt")
        if span.font_size is not None and span.font_size != block_size:
            css.append(f"font-size: {span.font_size}pt")
        if span.font_family and span.font_family != block_family:
            safe_family = span.font_family.replace('"', "'")
            css.append(f"font-family: {safe_family}")
        if span.font_style:
            css.append(f"font-style: {escape(span.font_style)}")
        if span.font_weight:
            css.append(f"font-weight: {escape(span.font_weight)}")

        escaped_text = escape(span.text)
        style = "; ".join(css)
        parts.append(f'<span style="{style}">{escaped_text}</span>')

    return "".join(parts)

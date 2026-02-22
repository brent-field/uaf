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
    # Rendering helpers
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

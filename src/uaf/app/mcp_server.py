"""MCP Server — AI agent interface to the UAF graph."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import Artifact, NodeType, make_node_metadata
from uaf.core.serialization import node_to_dict

if TYPE_CHECKING:
    from uaf.app.lenses import LensRegistry
    from uaf.security.secure_graph_db import SecureGraphDB, Session


def create_mcp_server(
    db: SecureGraphDB, session: Session, registry: LensRegistry
) -> FastMCP:
    """Create an MCP server wired to a SecureGraphDB and LensRegistry.

    The session is fixed at creation time (service account pattern).
    """
    mcp = FastMCP("uaf")

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @mcp.tool()
    def create_artifact(title: str) -> str:
        """Create a new artifact and return its ID as JSON."""
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)
        art_id = db.create_node(session, art)
        return json.dumps({"artifact_id": str(art_id), "title": title})

    @mcp.tool()
    def get_artifact(artifact_id: str) -> str:
        """Get an artifact with summary of its children."""
        nid = NodeId(value=uuid.UUID(artifact_id))
        art = db.get_node(session, nid)
        if art is None:
            return json.dumps({"error": "not found"})
        children = db.get_children(session, nid)
        return json.dumps({
            "artifact": node_to_dict(art),
            "child_count": len(children),
            "children_summary": [
                {"id": str(c.meta.id), "type": c.meta.node_type.value}
                for c in children
            ],
        }, default=str)

    @mcp.tool()
    def add_child(
        parent_id: str, node_type: str, text: str = "", title: str = "",
        value: str = "", row: int = 0, col: int = 0,
    ) -> str:
        """Create a child node and attach it with a CONTAINS edge."""
        from uaf.core.nodes import (
            Cell,
            CodeBlock,
            Heading,
            Paragraph,
            Sheet,
            TextBlock,
        )

        pid = NodeId(value=uuid.UUID(parent_id))
        nt = NodeType(node_type.lower())

        node: Any
        match nt:
            case NodeType.PARAGRAPH:
                node = Paragraph(meta=make_node_metadata(nt), text=text)
            case NodeType.HEADING:
                node = Heading(meta=make_node_metadata(nt), text=text, level=1)
            case NodeType.TEXT_BLOCK:
                node = TextBlock(meta=make_node_metadata(nt), text=text)
            case NodeType.CODE_BLOCK:
                node = CodeBlock(meta=make_node_metadata(nt), source=text, language="")
            case NodeType.SHEET:
                node = Sheet(meta=make_node_metadata(nt), title=title, rows=0, cols=0)
            case NodeType.CELL:
                node = Cell(meta=make_node_metadata(nt), value=value, row=row, col=col)
            case _:
                return json.dumps({"error": f"unsupported type: {node_type}"})

        nid = db.create_node(session, node)
        edge = Edge(
            id=EdgeId.generate(),
            source=pid,
            target=nid,
            edge_type=EdgeType.CONTAINS,
            created_at=utc_now(),
        )
        db.create_edge(session, edge)
        return json.dumps({"node_id": str(nid), "type": node_type}, default=str)

    @mcp.tool()
    def get_node(node_id: str) -> str:
        """Get a single node by ID."""
        nid = NodeId(value=uuid.UUID(node_id))
        node = db.get_node(session, nid)
        if node is None:
            return json.dumps({"error": "not found"})
        return json.dumps(node_to_dict(node), default=str)

    @mcp.tool()
    def get_children(node_id: str) -> str:
        """Get ordered children of a node."""
        nid = NodeId(value=uuid.UUID(node_id))
        children = db.get_children(session, nid)
        return json.dumps(
            [node_to_dict(c) for c in children], default=str,
        )

    @mcp.tool()
    def update_node(node_id: str, fields: dict[str, Any]) -> str:
        """Update node fields (text, value, title, etc.)."""
        nid = NodeId(value=uuid.UUID(node_id))
        node = db.get_node(session, nid)
        if node is None:
            return json.dumps({"error": "not found"})

        from dataclasses import replace

        # Update only supported fields
        updated = replace(node, **{k: v for k, v in fields.items() if k != "meta"})
        db.update_node(session, updated)
        return json.dumps({"updated": str(nid)})

    @mcp.tool()
    def delete_node(node_id: str) -> str:
        """Delete a node."""
        nid = NodeId(value=uuid.UUID(node_id))
        db.delete_node(session, nid)
        return json.dumps({"deleted": str(nid)})

    @mcp.tool()
    def find_by_type(node_type: str) -> str:
        """Find all nodes of a given type."""
        nt = NodeType(node_type.lower())
        nodes = db.find_by_type(session, nt)
        return json.dumps([node_to_dict(n) for n in nodes], default=str)

    @mcp.tool()
    def search(attribute: str, value: str) -> str:
        """Find nodes by attribute value."""
        nodes = db._db.find_by_attribute(attribute, value)
        return json.dumps([node_to_dict(n) for n in nodes], default=str)

    @mcp.tool()
    def get_references_to(node_id: str) -> str:
        """Find all nodes referencing a given node."""
        nid = NodeId(value=uuid.UUID(node_id))
        refs = db._db.get_references_to(nid)
        return json.dumps([node_to_dict(r) for r in refs], default=str)

    @mcp.tool()
    def get_history(node_id: str) -> str:
        """Get operation history for a node."""
        nid = NodeId(value=uuid.UUID(node_id))
        entries = db._db.get_history(nid)
        return json.dumps([
            {
                "operation_id": str(e.operation_id),
                "operation_type": type(e.operation).__name__,
                "timestamp": str(e.operation.timestamp),
            }
            for e in entries
        ])

    @mcp.tool()
    def render_artifact(artifact_id: str, lens_type: str) -> str:
        """Render an artifact through a lens. Returns HTML/text content."""
        lens = registry.get(lens_type)
        if lens is None:
            return json.dumps({"error": f"unknown lens: {lens_type}"})
        nid = NodeId(value=uuid.UUID(artifact_id))
        view = lens.render(db, session, nid)
        return json.dumps({
            "lens_type": view.lens_type,
            "title": view.title,
            "content": view.content,
            "content_type": view.content_type,
            "node_count": view.node_count,
        })

    @mcp.tool()
    def import_file(file_content: str, filename: str, format: str) -> str:
        """Import file content into the graph. Returns artifact ID."""
        from uaf.app.formats.csv_format import CsvHandler
        from uaf.app.formats.markdown import MarkdownHandler
        from uaf.app.formats.plaintext import PlainTextHandler

        handlers: dict[str, MarkdownHandler | CsvHandler | PlainTextHandler] = {
            "markdown": MarkdownHandler(),
            "csv": CsvHandler(),
            "plaintext": PlainTextHandler(),
        }
        handler = handlers.get(format)
        if handler is None:
            return json.dumps({"error": f"unknown format: {format}"})

        exts = {"markdown": ".md", "csv": ".csv", "plaintext": ".txt"}
        suffix = exts.get(format, ".txt")
        stem = Path(filename).stem

        with tempfile.NamedTemporaryFile(
            prefix=f"{stem}_", suffix=suffix, delete=False,
        ) as tmp:
            tmp.write(file_content.encode("utf-8"))
            tmp_path = Path(tmp.name)

        final_path = tmp_path.parent / f"{stem}{suffix}"
        tmp_path.rename(final_path)

        try:
            art_id = handler.import_file(final_path, db._db)
        finally:
            final_path.unlink(missing_ok=True)

        return json.dumps({"artifact_id": str(art_id)})

    @mcp.tool()
    def export_file(artifact_id: str, format: str) -> str:
        """Export an artifact to a file format. Returns the content as text."""
        from uaf.app.formats.csv_format import CsvHandler
        from uaf.app.formats.markdown import MarkdownHandler
        from uaf.app.formats.plaintext import PlainTextHandler

        handlers: dict[str, MarkdownHandler | CsvHandler | PlainTextHandler] = {
            "markdown": MarkdownHandler(),
            "csv": CsvHandler(),
            "plaintext": PlainTextHandler(),
        }
        handler = handlers.get(format)
        if handler is None:
            return json.dumps({"error": f"unknown format: {format}"})

        nid = NodeId(value=uuid.UUID(artifact_id))
        exts = {"markdown": ".md", "csv": ".csv", "plaintext": ".txt"}
        suffix = exts.get(format, ".txt")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            handler.export_file(db._db, nid, tmp_path)
            content = tmp_path.read_text(encoding="utf-8")
        finally:
            tmp_path.unlink(missing_ok=True)

        return content

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    @mcp.resource("uaf://artifacts")
    def list_artifacts() -> str:
        """List all artifacts."""
        artifacts = db.find_by_type(session, NodeType.ARTIFACT)
        return json.dumps([
            {"id": str(a.meta.id), "title": a.title}
            for a in artifacts
            if isinstance(a, Artifact)
        ], default=str)

    @mcp.resource("uaf://artifacts/{artifact_id}")
    def artifact_detail(artifact_id: str) -> str:
        """Get artifact details with children summary."""
        nid = NodeId(value=uuid.UUID(artifact_id))
        art = db.get_node(session, nid)
        if art is None:
            return json.dumps({"error": "not found"})
        children = db.get_children(session, nid)
        return json.dumps({
            "artifact": node_to_dict(art),
            "children": [node_to_dict(c) for c in children],
        }, default=str)

    @mcp.resource("uaf://artifacts/{artifact_id}/doc")
    def artifact_doc(artifact_id: str) -> str:
        """DocLens rendered view."""
        lens = registry.get("doc")
        if lens is None:
            return json.dumps({"error": "doc lens not registered"})
        nid = NodeId(value=uuid.UUID(artifact_id))
        view = lens.render(db, session, nid)
        return view.content

    @mcp.resource("uaf://artifacts/{artifact_id}/grid")
    def artifact_grid(artifact_id: str) -> str:
        """GridLens rendered view."""
        lens = registry.get("grid")
        if lens is None:
            return json.dumps({"error": "grid lens not registered"})
        nid = NodeId(value=uuid.UUID(artifact_id))
        view = lens.render(db, session, nid)
        return view.content

    return mcp

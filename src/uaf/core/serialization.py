"""Deterministic serialization, deserialization, and content hashing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from uaf.core.edges import Edge, EdgeType
from uaf.core.errors import SerializationError
from uaf.core.node_id import BlobId, EdgeId, NodeId, OperationId
from uaf.core.nodes import (
    Artifact,
    ArtifactACL,
    Cell,
    CodeBlock,
    FormulaCell,
    Heading,
    Image,
    LayoutHint,
    NodeMetadata,
    NodeType,
    Paragraph,
    RawNode,
    Shape,
    Sheet,
    Slide,
    Task,
    TextBlock,
)

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Node-type registry
# ---------------------------------------------------------------------------

_NODE_TYPE_NAME: dict[type[Any], str] = {
    Artifact: "Artifact",
    Paragraph: "Paragraph",
    Heading: "Heading",
    TextBlock: "TextBlock",
    Cell: "Cell",
    FormulaCell: "FormulaCell",
    Sheet: "Sheet",
    CodeBlock: "CodeBlock",
    Task: "Task",
    Slide: "Slide",
    Shape: "Shape",
    Image: "Image",
    ArtifactACL: "ArtifactACL",
    RawNode: "RawNode",
}

_NAME_TO_NODE_TYPE: dict[str, type[Any]] = {v: k for k, v in _NODE_TYPE_NAME.items()}


# ---------------------------------------------------------------------------
# Layout serialization
# ---------------------------------------------------------------------------


def _layout_to_dict(layout: LayoutHint) -> dict[str, Any]:
    d: dict[str, Any] = {}
    if layout.page is not None:
        d["page"] = layout.page
    if layout.x is not None:
        d["x"] = layout.x
    if layout.y is not None:
        d["y"] = layout.y
    if layout.width is not None:
        d["width"] = layout.width
    if layout.height is not None:
        d["height"] = layout.height
    if layout.font_family is not None:
        d["font_family"] = layout.font_family
    if layout.font_size is not None:
        d["font_size"] = layout.font_size
    if layout.font_weight is not None:
        d["font_weight"] = layout.font_weight
    if layout.font_style is not None:
        d["font_style"] = layout.font_style
    if layout.color is not None:
        d["color"] = layout.color
    if layout.reading_order is not None:
        d["reading_order"] = layout.reading_order
    if layout.rotation is not None:
        d["rotation"] = layout.rotation
    if layout.first_line_weight is not None:
        d["first_line_weight"] = layout.first_line_weight
    if layout.header_footer:
        d["header_footer"] = True
    if layout.display_text is not None:
        d["display_text"] = layout.display_text
    return d


def _layout_from_dict(d: dict[str, Any]) -> LayoutHint:
    return LayoutHint(
        page=d.get("page"),
        x=d.get("x"),
        y=d.get("y"),
        width=d.get("width"),
        height=d.get("height"),
        font_family=d.get("font_family"),
        font_size=d.get("font_size"),
        font_weight=d.get("font_weight"),
        font_style=d.get("font_style"),
        color=d.get("color"),
        reading_order=d.get("reading_order"),
        rotation=d.get("rotation"),
        first_line_weight=d.get("first_line_weight"),
        header_footer=bool(d.get("header_footer", False)),
        display_text=d.get("display_text"),
    )


# ---------------------------------------------------------------------------
# Metadata serialization
# ---------------------------------------------------------------------------


def _meta_to_dict(meta: NodeMetadata) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": str(meta.id.value),
        "node_type": meta.node_type.value,
        "created_at": meta.created_at.isoformat(),
        "updated_at": meta.updated_at.isoformat(),
    }
    if meta.owner is not None:
        d["owner"] = meta.owner
    if meta.layout is not None:
        d["layout"] = _layout_to_dict(meta.layout)
    return d


def _meta_from_dict(d: dict[str, Any]) -> NodeMetadata:
    layout = _layout_from_dict(d["layout"]) if "layout" in d else None
    return NodeMetadata(
        id=NodeId(value=__import__("uuid").UUID(d["id"])),
        node_type=NodeType(d["node_type"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        owner=d.get("owner"),
        layout=layout,
    )


# ---------------------------------------------------------------------------
# Node serialization
# ---------------------------------------------------------------------------


def node_to_dict(node: Any) -> dict[str, Any]:
    """Serialize any NodeData to a dict with a __type__ discriminator."""
    type_name = _NODE_TYPE_NAME.get(type(node))
    if type_name is None:
        msg = f"Unknown node type: {type(node)}"
        raise SerializationError(msg)

    d: dict[str, Any] = {
        "__type__": type_name,
        "__schema_version__": SCHEMA_VERSION,
        "meta": _meta_to_dict(node.meta),
    }

    match node:
        case Artifact(title=title):
            d["title"] = title
        case Paragraph(text=text, style=style):
            d["text"] = text
            d["style"] = style
        case Heading(text=text, level=level):
            d["text"] = text
            d["level"] = level
        case TextBlock(text=text, format=fmt):
            d["text"] = text
            d["format"] = fmt
        case Cell(value=value, row=row, col=col):
            d["value"] = value
            d["row"] = row
            d["col"] = col
        case FormulaCell(formula=formula, cached_value=cached_value, row=row, col=col):
            d["formula"] = formula
            d["cached_value"] = cached_value
            d["row"] = row
            d["col"] = col
        case Sheet(title=title, rows=rows, cols=cols):
            d["title"] = title
            d["rows"] = rows
            d["cols"] = cols
        case CodeBlock(source=source, language=language):
            d["source"] = source
            d["language"] = language
        case Task(title=title, completed=completed, due_date=due_date):
            d["title"] = title
            d["completed"] = completed
            d["due_date"] = due_date.isoformat() if due_date is not None else None
        case Slide(title=title, order=order):
            d["title"] = title
            d["order"] = order
        case Shape(shape_type=shape_type, x=x, y=y, width=width, height=height):
            d["shape_type"] = shape_type
            d["x"] = x
            d["y"] = y
            d["width"] = width
            d["height"] = height
        case Image(uri=uri, alt_text=alt_text, width=width, height=height):
            d["uri"] = uri
            d["alt_text"] = alt_text
            d["width"] = width
            d["height"] = height
        case ArtifactACL(default_role=default_role, public_read=public_read):
            d["default_role"] = default_role
            d["public_read"] = public_read
        case RawNode(raw=raw, original_type=original_type):
            d["raw"] = raw
            d["original_type"] = original_type

    return d


def node_from_dict(d: dict[str, Any]) -> Any:
    """Deserialize a dict to a NodeData instance. Unknown __type__ yields RawNode."""
    type_name = d.get("__type__")
    if type_name is None:
        msg = "Missing '__type__' in serialized node"
        raise SerializationError(msg)

    meta = _meta_from_dict(d["meta"])
    node_cls = _NAME_TO_NODE_TYPE.get(type_name)

    if node_cls is None:
        # Unknown type — wrap as RawNode for forward compatibility
        raw_meta = NodeMetadata(
            id=meta.id,
            node_type=NodeType.RAW,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
            owner=meta.owner,
            layout=meta.layout,
        )
        return RawNode(meta=raw_meta, raw=d, original_type=type_name)

    match node_cls:
        case _ if node_cls is Artifact:
            return Artifact(meta=meta, title=d["title"])
        case _ if node_cls is Paragraph:
            return Paragraph(meta=meta, text=d["text"], style=d.get("style", "body"))
        case _ if node_cls is Heading:
            return Heading(meta=meta, text=d["text"], level=d["level"])
        case _ if node_cls is TextBlock:
            return TextBlock(meta=meta, text=d["text"], format=d.get("format", "plain"))
        case _ if node_cls is Cell:
            return Cell(meta=meta, value=d["value"], row=d["row"], col=d["col"])
        case _ if node_cls is FormulaCell:
            return FormulaCell(
                meta=meta,
                formula=d["formula"],
                cached_value=d["cached_value"],
                row=d["row"],
                col=d["col"],
            )
        case _ if node_cls is Sheet:
            return Sheet(meta=meta, title=d["title"], rows=d["rows"], cols=d["cols"])
        case _ if node_cls is CodeBlock:
            return CodeBlock(meta=meta, source=d["source"], language=d["language"])
        case _ if node_cls is Task:
            due = d.get("due_date")
            due_dt = datetime.fromisoformat(due) if due is not None else None
            return Task(meta=meta, title=d["title"], completed=d["completed"], due_date=due_dt)
        case _ if node_cls is Slide:
            return Slide(meta=meta, title=d["title"], order=d["order"])
        case _ if node_cls is Shape:
            return Shape(
                meta=meta,
                shape_type=d["shape_type"],
                x=d["x"],
                y=d["y"],
                width=d["width"],
                height=d["height"],
            )
        case _ if node_cls is Image:
            return Image(
                meta=meta,
                uri=d["uri"],
                alt_text=d.get("alt_text", ""),
                width=d.get("width"),
                height=d.get("height"),
            )
        case _ if node_cls is ArtifactACL:
            return ArtifactACL(
                meta=meta,
                default_role=d.get("default_role"),
                public_read=d.get("public_read", False),
            )
        case _ if node_cls is RawNode:
            return RawNode(meta=meta, raw=d["raw"], original_type=d["original_type"])
        case _:  # pragma: no cover
            msg = f"Unhandled node type in deserialization: {type_name}"
            raise SerializationError(msg)


# ---------------------------------------------------------------------------
# Edge serialization
# ---------------------------------------------------------------------------


def edge_to_dict(edge: Edge) -> dict[str, Any]:
    """Serialize an Edge to a dict."""
    return {
        "__type__": "Edge",
        "__schema_version__": SCHEMA_VERSION,
        "id": str(edge.id.value),
        "source": str(edge.source.value),
        "target": str(edge.target.value),
        "edge_type": edge.edge_type.value,
        "created_at": edge.created_at.isoformat(),
        "properties": [[k, v] for k, v in edge.properties],
    }


def edge_from_dict(d: dict[str, Any]) -> Edge:
    """Deserialize a dict to an Edge."""
    import uuid

    props = tuple((k, v) for k, v in d["properties"])
    return Edge(
        id=EdgeId(value=uuid.UUID(d["id"])),
        source=NodeId(value=uuid.UUID(d["source"])),
        target=NodeId(value=uuid.UUID(d["target"])),
        edge_type=EdgeType(d["edge_type"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        properties=props,
    )


# ---------------------------------------------------------------------------
# Canonical JSON + content hashing
# ---------------------------------------------------------------------------


def canonical_json(data: dict[str, Any]) -> bytes:
    """Produce deterministic JSON: sorted keys, no whitespace, UTF-8."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def content_hash(data: dict[str, Any]) -> OperationId:
    """Compute the SHA-256 content hash of a canonical JSON representation."""
    return OperationId(hex_digest=hashlib.sha256(canonical_json(data)).hexdigest())


def blob_hash(data: bytes) -> BlobId:
    """Compute the SHA-256 content hash of raw bytes."""
    return BlobId(hex_digest=hashlib.sha256(data).hexdigest())

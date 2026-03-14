"""Tests for serialization — round-trips, deterministic hashing, schema evolution."""

from __future__ import annotations

import pytest

from uaf.core.edges import Edge, EdgeType
from uaf.core.errors import SerializationError
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    ArtifactACL,
    Cell,
    CodeBlock,
    FormulaCell,
    Heading,
    Image,
    LayoutHint,
    MathBlock,
    NodeType,
    Paragraph,
    RawNode,
    Shape,
    Sheet,
    Slide,
    SpanInfo,
    Task,
    TextBlock,
    make_node_metadata,
)
from uaf.core.serialization import (
    SCHEMA_VERSION,
    blob_hash,
    canonical_json,
    content_hash,
    edge_from_dict,
    edge_to_dict,
    node_from_dict,
    node_to_dict,
)

# ---------------------------------------------------------------------------
# Round-trip helpers
# ---------------------------------------------------------------------------


def _roundtrip_node(node: object) -> object:
    d = node_to_dict(node)
    return node_from_dict(d)


def _roundtrip_edge(edge: Edge) -> Edge:
    d = edge_to_dict(edge)
    return edge_from_dict(d)


# ---------------------------------------------------------------------------
# Node round-trip tests
# ---------------------------------------------------------------------------


class TestNodeRoundTrip:
    def test_artifact(self) -> None:
        orig = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        assert _roundtrip_node(orig) == orig

    def test_paragraph(self) -> None:
        orig = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Hi", style="quote")
        assert _roundtrip_node(orig) == orig

    def test_heading(self) -> None:
        orig = Heading(meta=make_node_metadata(NodeType.HEADING), text="H1", level=1)
        assert _roundtrip_node(orig) == orig

    def test_text_block(self) -> None:
        orig = TextBlock(meta=make_node_metadata(NodeType.TEXT_BLOCK), text="X", format="html")
        assert _roundtrip_node(orig) == orig

    def test_cell_string(self) -> None:
        orig = Cell(meta=make_node_metadata(NodeType.CELL), value="hello", row=0, col=0)
        assert _roundtrip_node(orig) == orig

    def test_cell_int(self) -> None:
        orig = Cell(meta=make_node_metadata(NodeType.CELL), value=42, row=1, col=2)
        assert _roundtrip_node(orig) == orig

    def test_cell_none(self) -> None:
        orig = Cell(meta=make_node_metadata(NodeType.CELL), value=None, row=0, col=0)
        assert _roundtrip_node(orig) == orig

    def test_formula_cell(self) -> None:
        orig = FormulaCell(
            meta=make_node_metadata(NodeType.FORMULA_CELL),
            formula="=SUM(A1:A5)",
            cached_value=100.0,
            row=0,
            col=0,
        )
        assert _roundtrip_node(orig) == orig

    def test_sheet(self) -> None:
        orig = Sheet(meta=make_node_metadata(NodeType.SHEET), title="S1", rows=10, cols=5)
        assert _roundtrip_node(orig) == orig

    def test_code_block(self) -> None:
        orig = CodeBlock(
            meta=make_node_metadata(NodeType.CODE_BLOCK),
            source="x = 1",
            language="python",
        )
        assert _roundtrip_node(orig) == orig

    def test_math_block(self) -> None:
        orig = MathBlock(
            meta=make_node_metadata(NodeType.MATH_BLOCK),
            source="E = mc^2",
            equation_number="(3)",
        )
        assert _roundtrip_node(orig) == orig

    def test_math_block_no_equation_number(self) -> None:
        orig = MathBlock(
            meta=make_node_metadata(NodeType.MATH_BLOCK),
            source="x + y",
        )
        assert _roundtrip_node(orig) == orig

    def test_task_no_due_date(self) -> None:
        orig = Task(meta=make_node_metadata(NodeType.TASK), title="Do it", completed=False)
        assert _roundtrip_node(orig) == orig

    def test_task_with_due_date(self) -> None:
        orig = Task(
            meta=make_node_metadata(NodeType.TASK),
            title="Do it",
            completed=True,
            due_date=utc_now(),
        )
        assert _roundtrip_node(orig) == orig

    def test_slide(self) -> None:
        orig = Slide(meta=make_node_metadata(NodeType.SLIDE), title="Slide 1", order=0)
        assert _roundtrip_node(orig) == orig

    def test_shape(self) -> None:
        orig = Shape(
            meta=make_node_metadata(NodeType.SHAPE),
            shape_type="rect",
            x=10.0,
            y=20.0,
            width=100.0,
            height=50.0,
        )
        assert _roundtrip_node(orig) == orig

    def test_image(self) -> None:
        orig = Image(
            meta=make_node_metadata(NodeType.IMAGE),
            uri="blob:abc",
            alt_text="photo",
            width=800,
            height=600,
        )
        assert _roundtrip_node(orig) == orig

    def test_artifact_acl(self) -> None:
        orig = ArtifactACL(
            meta=make_node_metadata(NodeType.ARTIFACT_ACL),
            default_role="viewer",
            public_read=True,
        )
        assert _roundtrip_node(orig) == orig

    def test_raw_node(self) -> None:
        orig = RawNode(
            meta=make_node_metadata(NodeType.RAW),
            raw={"custom": "data"},
            original_type="FutureWidget",
        )
        assert _roundtrip_node(orig) == orig


class TestNodeWithLayout:
    def test_roundtrip_with_layout(self) -> None:
        layout = LayoutHint(page=1, x=10.0, y=20.0, font_size=12.0)
        meta = make_node_metadata(NodeType.PARAGRAPH, layout=layout)
        orig = Paragraph(meta=meta, text="With layout")
        result = _roundtrip_node(orig)
        assert result == orig

    def test_roundtrip_with_spans(self) -> None:
        spans = (
            SpanInfo(text="E", font_family="Symbol, serif", font_size=12.0),
            SpanInfo(text=" = mc", font_size=12.0, y_offset=8.5),
        )
        layout = LayoutHint(page=0, x=72.0, y=100.0, spans=spans)
        meta = make_node_metadata(NodeType.PARAGRAPH, layout=layout)
        orig = Paragraph(meta=meta, text="E = mc")
        result = _roundtrip_node(orig)
        assert result == orig

    def test_roundtrip_with_line_height(self) -> None:
        layout = LayoutHint(page=0, x=72.0, y=100.0, line_height=14.5)
        meta = make_node_metadata(NodeType.PARAGRAPH, layout=layout)
        orig = Paragraph(meta=meta, text="With line height")
        result = _roundtrip_node(orig)
        assert result == orig


class TestUnknownType:
    def test_unknown_type_yields_raw_node(self) -> None:
        d = {
            "__type__": "QuantumWidget",
            "__schema_version__": 99,
            "meta": {
                "id": str(NodeId.generate().value),
                "node_type": "raw",
                "created_at": utc_now().isoformat(),
                "updated_at": utc_now().isoformat(),
            },
            "some_field": "hello",
        }
        result = node_from_dict(d)
        assert isinstance(result, RawNode)
        assert result.original_type == "QuantumWidget"
        assert result.raw == d


class TestSchemaVersion:
    def test_schema_version_in_output(self) -> None:
        node = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="T")
        d = node_to_dict(node)
        assert d["__schema_version__"] == SCHEMA_VERSION


class TestMissingType:
    def test_missing_type_raises(self) -> None:
        with pytest.raises(SerializationError, match="__type__"):
            node_from_dict({"meta": {}})


# ---------------------------------------------------------------------------
# Edge round-trip tests
# ---------------------------------------------------------------------------


class TestEdgeRoundTrip:
    def test_basic_edge(self) -> None:
        orig = Edge(
            id=EdgeId.generate(),
            source=NodeId.generate(),
            target=NodeId.generate(),
            edge_type=EdgeType.CONTAINS,
            created_at=utc_now(),
        )
        assert _roundtrip_edge(orig) == orig

    def test_edge_with_properties(self) -> None:
        orig = Edge(
            id=EdgeId.generate(),
            source=NodeId.generate(),
            target=NodeId.generate(),
            edge_type=EdgeType.LINKED_TO,
            created_at=utc_now(),
            properties=(("weight", 3), ("label", "cross-ref")),
        )
        assert _roundtrip_edge(orig) == orig


# ---------------------------------------------------------------------------
# Canonical JSON + hashing tests
# ---------------------------------------------------------------------------


class TestCanonicalJson:
    def test_deterministic(self) -> None:
        d = {"b": 2, "a": 1}
        assert canonical_json(d) == canonical_json(d)

    def test_sorted_keys(self) -> None:
        result = canonical_json({"b": 2, "a": 1})
        assert result == b'{"a":1,"b":2}'

    def test_no_whitespace(self) -> None:
        result = canonical_json({"key": "value"})
        assert b" " not in result


class TestContentHash:
    def test_deterministic_hash(self) -> None:
        d = {"foo": "bar"}
        h1 = content_hash(d)
        h2 = content_hash(d)
        assert h1 == h2

    def test_different_data_different_hash(self) -> None:
        h1 = content_hash({"a": 1})
        h2 = content_hash({"a": 2})
        assert h1 != h2

    def test_hash_is_operation_id(self) -> None:
        from uaf.core.node_id import OperationId

        h = content_hash({"x": "y"})
        assert isinstance(h, OperationId)


class TestNewNodeTypeSerialization:
    def test_bullet_list_item_round_trip(self) -> None:
        from uaf.core.nodes import BulletListItem

        node = BulletListItem(
            meta=make_node_metadata(NodeType.BULLET_LIST_ITEM),
            text="Buy groceries",
            indent_level=1,
            content_format="html",
        )
        d = node_to_dict(node)
        restored = node_from_dict(d)
        assert isinstance(restored, BulletListItem)
        assert restored.text == "Buy groceries"
        assert restored.indent_level == 1
        assert restored.content_format == "html"

    def test_numbered_list_item_round_trip(self) -> None:
        from uaf.core.nodes import NumberedListItem

        node = NumberedListItem(
            meta=make_node_metadata(NodeType.NUMBERED_LIST_ITEM),
            text="Step one",
            indent_level=0,
            content_format="plain",
        )
        d = node_to_dict(node)
        restored = node_from_dict(d)
        assert isinstance(restored, NumberedListItem)
        assert restored.text == "Step one"
        assert restored.indent_level == 0

    def test_blockquote_round_trip(self) -> None:
        from uaf.core.nodes import Blockquote

        node = Blockquote(
            meta=make_node_metadata(NodeType.BLOCKQUOTE),
            text="To be or not to be",
            content_format="plain",
        )
        d = node_to_dict(node)
        restored = node_from_dict(d)
        assert isinstance(restored, Blockquote)
        assert restored.text == "To be or not to be"

    def test_divider_round_trip(self) -> None:
        from uaf.core.nodes import Divider

        node = Divider(meta=make_node_metadata(NodeType.DIVIDER))
        d = node_to_dict(node)
        restored = node_from_dict(d)
        assert isinstance(restored, Divider)


class TestBlobHash:
    def test_deterministic(self) -> None:
        data = b"hello world"
        h1 = blob_hash(data)
        h2 = blob_hash(data)
        assert h1 == h2

    def test_different_data_different_hash(self) -> None:
        h1 = blob_hash(b"hello")
        h2 = blob_hash(b"world")
        assert h1 != h2

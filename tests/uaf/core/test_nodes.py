"""Tests for node types, NodeMetadata, LayoutHint, and the NodeData union."""

from datetime import datetime

import pytest

from uaf.core.node_id import NodeId, utc_now
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
    NodeMetadata,
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


class TestNodeType:
    def test_all_types_exist(self) -> None:
        expected = {
            "ARTIFACT",
            "PARAGRAPH",
            "HEADING",
            "TEXT_BLOCK",
            "CELL",
            "FORMULA_CELL",
            "SHEET",
            "CODE_BLOCK",
            "MATH_BLOCK",
            "TASK",
            "SLIDE",
            "SHAPE",
            "IMAGE",
            "ARTIFACT_ACL",
            "RAW",
        }
        actual = {t.name for t in NodeType}
        assert actual == expected

    def test_values_are_lowercase_strings(self) -> None:
        for t in NodeType:
            assert t.value == t.name.lower()


class TestLayoutHint:
    def test_defaults_are_none(self) -> None:
        hint = LayoutHint()
        assert hint.page is None
        assert hint.x is None
        assert hint.reading_order is None

    def test_with_values(self) -> None:
        hint = LayoutHint(page=1, x=10.0, y=20.0, width=100.0, height=50.0)
        assert hint.page == 1
        assert hint.x == 10.0

    def test_is_frozen(self) -> None:
        hint = LayoutHint()
        with pytest.raises(AttributeError):
            hint.page = 1  # type: ignore[misc]


class TestNodeMetadata:
    def test_construction(self) -> None:
        nid = NodeId.generate()
        now = utc_now()
        meta = NodeMetadata(
            id=nid,
            node_type=NodeType.PARAGRAPH,
            created_at=now,
            updated_at=now,
        )
        assert meta.id == nid
        assert meta.node_type == NodeType.PARAGRAPH
        assert meta.owner is None
        assert meta.layout is None

    def test_with_owner(self) -> None:
        meta = make_node_metadata(NodeType.ARTIFACT, owner="alice")
        assert meta.owner == "alice"

    def test_with_layout(self) -> None:
        layout = LayoutHint(page=2, x=5.0)
        meta = make_node_metadata(NodeType.IMAGE, layout=layout)
        assert meta.layout is not None
        assert meta.layout.page == 2

    def test_is_frozen(self) -> None:
        meta = make_node_metadata(NodeType.ARTIFACT)
        with pytest.raises(AttributeError):
            meta.owner = "bob"  # type: ignore[misc]


class TestMakeNodeMetadata:
    def test_generates_id(self) -> None:
        meta = make_node_metadata(NodeType.ARTIFACT)
        assert isinstance(meta.id, NodeId)

    def test_timestamps_are_utc(self) -> None:
        meta = make_node_metadata(NodeType.ARTIFACT)
        assert meta.created_at.tzinfo is not None
        assert meta.updated_at.tzinfo is not None

    def test_created_and_updated_match(self) -> None:
        meta = make_node_metadata(NodeType.ARTIFACT)
        assert meta.created_at == meta.updated_at

    def test_custom_node_id(self) -> None:
        nid = NodeId.generate()
        meta = make_node_metadata(NodeType.ARTIFACT, node_id=nid)
        assert meta.id == nid


class TestArtifact:
    def test_construction(self) -> None:
        meta = make_node_metadata(NodeType.ARTIFACT)
        art = Artifact(meta=meta, title="My Doc")
        assert art.title == "My Doc"
        assert art.meta.node_type == NodeType.ARTIFACT

    def test_is_frozen(self) -> None:
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="T")
        with pytest.raises(AttributeError):
            art.title = "X"  # type: ignore[misc]


class TestParagraph:
    def test_default_style(self) -> None:
        p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Hello")
        assert p.style == "body"

    def test_custom_style(self) -> None:
        p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Hi", style="quote")
        assert p.style == "quote"


class TestCell:
    def test_string_value(self) -> None:
        c = Cell(meta=make_node_metadata(NodeType.CELL), value="foo", row=0, col=0)
        assert c.value == "foo"

    def test_int_value(self) -> None:
        c = Cell(meta=make_node_metadata(NodeType.CELL), value=42, row=1, col=2)
        assert c.value == 42

    def test_none_value(self) -> None:
        c = Cell(meta=make_node_metadata(NodeType.CELL), value=None, row=0, col=0)
        assert c.value is None

    def test_bool_value(self) -> None:
        c = Cell(meta=make_node_metadata(NodeType.CELL), value=True, row=0, col=0)
        assert c.value is True


class TestFormulaCell:
    def test_construction(self) -> None:
        fc = FormulaCell(
            meta=make_node_metadata(NodeType.FORMULA_CELL),
            formula="=SUM(A1:A10)",
            cached_value=55.0,
            row=0,
            col=0,
        )
        assert fc.formula == "=SUM(A1:A10)"
        assert fc.cached_value == 55.0


class TestTask:
    def test_defaults(self) -> None:
        t = Task(meta=make_node_metadata(NodeType.TASK), title="Do stuff")
        assert t.completed is False
        assert t.due_date is None

    def test_with_due_date(self) -> None:
        now = utc_now()
        t = Task(meta=make_node_metadata(NodeType.TASK), title="Do stuff", due_date=now)
        assert isinstance(t.due_date, datetime)


class TestRawNode:
    def test_construction(self) -> None:
        meta = make_node_metadata(NodeType.RAW)
        raw = RawNode(meta=meta, raw={"key": "val"}, original_type="FutureNode")
        assert raw.original_type == "FutureNode"
        assert raw.raw == {"key": "val"}


class TestNodeDataUnionExhaustiveness:
    """Verify all concrete types are handled in match statements."""

    def _match_node(
        self,
        node: Artifact
        | Paragraph
        | Heading
        | TextBlock
        | Cell
        | FormulaCell
        | Sheet
        | CodeBlock
        | MathBlock
        | Task
        | Slide
        | Shape
        | Image
        | ArtifactACL
        | RawNode,
    ) -> str:
        match node:
            case Artifact():
                return "artifact"
            case Paragraph():
                return "paragraph"
            case Heading():
                return "heading"
            case TextBlock():
                return "text_block"
            case Cell():
                return "cell"
            case FormulaCell():
                return "formula_cell"
            case Sheet():
                return "sheet"
            case CodeBlock():
                return "code_block"
            case MathBlock():
                return "math_block"
            case Task():
                return "task"
            case Slide():
                return "slide"
            case Shape():
                return "shape"
            case Image():
                return "image"
            case ArtifactACL():
                return "artifact_acl"
            case RawNode():
                return "raw"

    def test_match_artifact(self) -> None:
        node = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="T")
        assert self._match_node(node) == "artifact"

    def test_match_heading(self) -> None:
        node = Heading(meta=make_node_metadata(NodeType.HEADING), text="H", level=1)
        assert self._match_node(node) == "heading"

    def test_match_sheet(self) -> None:
        node = Sheet(meta=make_node_metadata(NodeType.SHEET), title="S", rows=10, cols=5)
        assert self._match_node(node) == "sheet"

    def test_match_image(self) -> None:
        node = Image(meta=make_node_metadata(NodeType.IMAGE), uri="blob:abc")
        assert self._match_node(node) == "image"

    def test_match_math_block(self) -> None:
        node = MathBlock(meta=make_node_metadata(NodeType.MATH_BLOCK), source="E=mc^2")
        assert self._match_node(node) == "math_block"


class TestSpanInfo:
    def test_defaults(self) -> None:
        s = SpanInfo(text="hello")
        assert s.text == "hello"
        assert s.font_size is None
        assert s.font_family is None
        assert s.y_offset is None

    def test_with_all_fields(self) -> None:
        s = SpanInfo(
            text="E",
            font_size=12.0,
            font_family="Symbol, serif",
            font_weight="bold",
            font_style="italic",
            y_offset=-3.0,
        )
        assert s.font_weight == "bold"
        assert s.y_offset == -3.0

    def test_is_frozen(self) -> None:
        s = SpanInfo(text="x")
        with pytest.raises(AttributeError):
            s.text = "y"  # type: ignore[misc]


class TestMathBlock:
    def test_construction(self) -> None:
        meta = make_node_metadata(NodeType.MATH_BLOCK)
        mb = MathBlock(meta=meta, source="E = mc^2")
        assert mb.source == "E = mc^2"
        assert mb.equation_number is None
        assert mb.display == "block"

    def test_with_equation_number(self) -> None:
        meta = make_node_metadata(NodeType.MATH_BLOCK)
        mb = MathBlock(meta=meta, source="x^2", equation_number="(3)")
        assert mb.equation_number == "(3)"

    def test_is_frozen(self) -> None:
        mb = MathBlock(meta=make_node_metadata(NodeType.MATH_BLOCK), source="x=1")
        with pytest.raises(AttributeError):
            mb.source = "y=2"  # type: ignore[misc]


class TestLayoutHintSpans:
    def test_spans_default_none(self) -> None:
        hint = LayoutHint()
        assert hint.spans is None

    def test_with_spans(self) -> None:
        spans = (SpanInfo(text="E", font_size=10.0),)
        hint = LayoutHint(spans=spans)
        assert hint.spans is not None
        assert len(hint.spans) == 1
        assert hint.spans[0].text == "E"

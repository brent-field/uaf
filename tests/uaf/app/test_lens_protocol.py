"""Tests for Lens protocol, LensView, LensRegistry, and LensAction types."""

from __future__ import annotations

from datetime import UTC, datetime

from uaf.app.lenses import Lens, LensRegistry, LensView
from uaf.app.lenses.actions import (
    DeleteColumn,
    DeleteNode,
    DeleteRow,
    DeleteText,
    FormatText,
    InsertColumn,
    InsertRow,
    InsertText,
    MoveNode,
    RenameArtifact,
    ReorderNodes,
    SetCellValue,
)
from uaf.core.node_id import NodeId, utc_now
from uaf.core.nodes import NodeType

# ---------------------------------------------------------------------------
# LensView tests
# ---------------------------------------------------------------------------


class TestLensView:
    def test_construction(self) -> None:
        nid = NodeId.generate()
        now = utc_now()
        view = LensView(
            lens_type="doc",
            artifact_id=nid,
            title="Test",
            content="<p>Hello</p>",
            content_type="text/html",
            node_count=1,
            rendered_at=now,
        )
        assert view.lens_type == "doc"
        assert view.artifact_id == nid
        assert view.title == "Test"
        assert view.content == "<p>Hello</p>"
        assert view.content_type == "text/html"
        assert view.node_count == 1
        assert view.rendered_at == now

    def test_frozen(self) -> None:
        nid = NodeId.generate()
        view = LensView(
            lens_type="doc",
            artifact_id=nid,
            title="Test",
            content="",
            content_type="text/html",
            node_count=0,
            rendered_at=utc_now(),
        )
        try:
            view.title = "changed"  # type: ignore[misc]
            raise AssertionError("Expected frozen")
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# LensRegistry tests
# ---------------------------------------------------------------------------


class _FakeLens:
    """Minimal lens for testing registry."""

    def __init__(self, lt: str, node_types: frozenset[NodeType]) -> None:
        self._lt = lt
        self._node_types = node_types

    @property
    def lens_type(self) -> str:
        return self._lt

    @property
    def supported_node_types(self) -> frozenset[NodeType]:
        return self._node_types

    def render(self, db: object, session: object, artifact_id: NodeId) -> LensView:
        return LensView(
            lens_type=self._lt,
            artifact_id=artifact_id,
            title="fake",
            content="",
            content_type="text/plain",
            node_count=0,
            rendered_at=datetime.now(tz=UTC),
        )

    def apply_action(
        self, db: object, session: object, artifact_id: NodeId, action: object
    ) -> None:
        pass


class TestLensRegistry:
    def test_register_and_get(self) -> None:
        reg = LensRegistry()
        lens = _FakeLens("doc", frozenset({NodeType.ARTIFACT, NodeType.PARAGRAPH}))
        reg.register(lens)
        assert reg.get("doc") is lens

    def test_get_missing_returns_none(self) -> None:
        reg = LensRegistry()
        assert reg.get("nonexistent") is None

    def test_available(self) -> None:
        reg = LensRegistry()
        reg.register(_FakeLens("grid", frozenset()))
        reg.register(_FakeLens("doc", frozenset()))
        assert reg.available() == ["doc", "grid"]

    def test_for_node_type(self) -> None:
        reg = LensRegistry()
        doc = _FakeLens("doc", frozenset({NodeType.ARTIFACT, NodeType.PARAGRAPH}))
        grid = _FakeLens("grid", frozenset({NodeType.ARTIFACT, NodeType.CELL}))
        reg.register(doc)
        reg.register(grid)
        # Both support ARTIFACT
        result = reg.for_node_type(NodeType.ARTIFACT)
        assert len(result) == 2
        # Only doc supports PARAGRAPH
        result = reg.for_node_type(NodeType.PARAGRAPH)
        assert len(result) == 1
        assert result[0].lens_type == "doc"

    def test_protocol_isinstance(self) -> None:
        lens = _FakeLens("doc", frozenset())
        assert isinstance(lens, Lens)


# ---------------------------------------------------------------------------
# LensAction type tests
# ---------------------------------------------------------------------------


class TestLensActionTypes:
    def test_insert_text(self) -> None:
        nid = NodeId.generate()
        action = InsertText(parent_id=nid, text="hello", position=0, style="paragraph")
        assert action.parent_id == nid
        assert action.text == "hello"
        assert action.position == 0
        assert action.style == "paragraph"

    def test_delete_text(self) -> None:
        nid = NodeId.generate()
        action = DeleteText(node_id=nid)
        assert action.node_id == nid

    def test_format_text(self) -> None:
        nid = NodeId.generate()
        action = FormatText(node_id=nid, style="heading", level=2)
        assert action.style == "heading"
        assert action.level == 2

    def test_set_cell_value(self) -> None:
        nid = NodeId.generate()
        action = SetCellValue(cell_id=nid, value=42)
        assert action.value == 42

    def test_insert_row(self) -> None:
        nid = NodeId.generate()
        action = InsertRow(sheet_id=nid, position=3)
        assert action.position == 3

    def test_insert_column(self) -> None:
        nid = NodeId.generate()
        action = InsertColumn(sheet_id=nid, position=1)
        assert action.position == 1

    def test_delete_row(self) -> None:
        nid = NodeId.generate()
        action = DeleteRow(sheet_id=nid, position=2)
        assert action.position == 2

    def test_delete_column(self) -> None:
        nid = NodeId.generate()
        action = DeleteColumn(sheet_id=nid, position=0)
        assert action.position == 0

    def test_reorder_nodes(self) -> None:
        nid = NodeId.generate()
        a, b = NodeId.generate(), NodeId.generate()
        action = ReorderNodes(parent_id=nid, new_order=(a, b))
        assert action.new_order == (a, b)

    def test_move_node(self) -> None:
        nid = NodeId.generate()
        new_parent = NodeId.generate()
        action = MoveNode(node_id=nid, new_parent_id=new_parent)
        assert action.new_parent_id == new_parent

    def test_delete_node(self) -> None:
        nid = NodeId.generate()
        action = DeleteNode(node_id=nid)
        assert action.node_id == nid

    def test_rename_artifact(self) -> None:
        nid = NodeId.generate()
        action = RenameArtifact(artifact_id=nid, title="New Name")
        assert action.title == "New Name"

    def test_all_actions_frozen(self) -> None:
        """Verify all action types are frozen."""
        nid = NodeId.generate()
        actions: list[object] = [
            InsertText(parent_id=nid, text="x", position=0),
            DeleteText(node_id=nid),
            FormatText(node_id=nid, style="heading"),
            SetCellValue(cell_id=nid, value=1),
            InsertRow(sheet_id=nid, position=0),
            InsertColumn(sheet_id=nid, position=0),
            DeleteRow(sheet_id=nid, position=0),
            DeleteColumn(sheet_id=nid, position=0),
            ReorderNodes(parent_id=nid, new_order=()),
            MoveNode(node_id=nid, new_parent_id=nid),
            DeleteNode(node_id=nid),
            RenameArtifact(artifact_id=nid, title="t"),
        ]
        for action in actions:
            try:
                object.__setattr__(action, "__test", True)
                raise AssertionError(f"{type(action).__name__} is not frozen")
            except AttributeError:
                pass

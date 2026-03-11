"""Tests for GridLens — spreadsheet rendering and editing."""

from __future__ import annotations

from uaf.app.lenses import Lens
from uaf.app.lenses.actions import (
    DeleteColumn,
    DeleteRow,
    InsertColumn,
    InsertRow,
    RenameArtifact,
    SetCellValue,
)
from uaf.app.lenses.grid_lens import GridLens
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Cell,
    FormulaCell,
    NodeType,
    Sheet,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB


def _setup() -> tuple[SecureGraphDB, object, GridLens]:
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    session = sdb.system_session()
    return sdb, session, GridLens()


def _contains(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


def _make_spreadsheet(
    sdb: SecureGraphDB, session: object
) -> tuple[NodeId, NodeId]:
    """Create a simple 2x3 spreadsheet. Returns (artifact_id, sheet_id)."""
    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Spreadsheet")
    art_id = sdb.create_node(session, art)

    sheet = Sheet(
        meta=make_node_metadata(NodeType.SHEET), title="Sheet1", rows=2, cols=3,
    )
    sheet_id = sdb.create_node(session, sheet)
    sdb.create_edge(session, _contains(art_id, sheet_id))

    # Row 0: A, B, C
    for c, val in enumerate(["A", "B", "C"]):
        cell = Cell(
            meta=make_node_metadata(NodeType.CELL), value=val, row=0, col=c,
        )
        cid = sdb.create_node(session, cell)
        sdb.create_edge(session, _contains(sheet_id, cid))

    # Row 1: 1, 2, 3
    for c, val in enumerate([1, 2, 3]):
        cell = Cell(
            meta=make_node_metadata(NodeType.CELL), value=val, row=1, col=c,
        )
        cid = sdb.create_node(session, cell)
        sdb.create_edge(session, _contains(sheet_id, cid))

    return art_id, sheet_id


class TestGridLensProtocol:
    def test_is_lens(self) -> None:
        assert isinstance(GridLens(), Lens)

    def test_lens_type(self) -> None:
        assert GridLens().lens_type == "grid"

    def test_supported_node_types(self) -> None:
        types = GridLens().supported_node_types
        assert NodeType.ARTIFACT in types
        assert NodeType.SHEET in types
        assert NodeType.CELL in types
        assert NodeType.FORMULA_CELL in types


class TestGridLensRender:
    def test_render_empty_artifact(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Empty")
        art_id = sdb.create_node(session, art)

        view = lens.render(sdb, session, art_id)
        assert view.lens_type == "grid"
        assert view.title == "Empty"
        assert view.node_count == 1
        assert view.content == ""

    def test_render_not_found(self) -> None:
        sdb, session, lens = _setup()
        view = lens.render(sdb, session, NodeId.generate())
        assert view.title == "(not found)"
        assert view.node_count == 0

    def test_render_with_cells(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        view = lens.render(sdb, session, art_id)
        assert view.lens_type == "grid"
        assert view.title == "Spreadsheet"
        # 1 artifact + 1 sheet + 6 cells
        assert view.node_count == 8
        assert "<table" in view.content
        assert f'data-sheet-id="{sheet_id}"' in view.content
        assert "<tr>" in view.content
        assert "<td" in view.content

    def test_render_cell_values(self) -> None:
        sdb, session, lens = _setup()
        art_id, _ = _make_spreadsheet(sdb, session)

        view = lens.render(sdb, session, art_id)
        assert ">A</td>" in view.content
        assert ">B</td>" in view.content
        assert ">1</td>" in view.content

    def test_render_with_formula_cell(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Formulas")
        art_id = sdb.create_node(session, art)

        sheet = Sheet(
            meta=make_node_metadata(NodeType.SHEET), title="S1", rows=1, cols=2,
        )
        sheet_id = sdb.create_node(session, sheet)
        sdb.create_edge(session, _contains(art_id, sheet_id))

        c1 = Cell(meta=make_node_metadata(NodeType.CELL), value=10, row=0, col=0)
        c1_id = sdb.create_node(session, c1)
        sdb.create_edge(session, _contains(sheet_id, c1_id))

        fc = FormulaCell(
            meta=make_node_metadata(NodeType.FORMULA_CELL),
            formula="=A1*2",
            cached_value=20,
            row=0,
            col=1,
        )
        fc_id = sdb.create_node(session, fc)
        sdb.create_edge(session, _contains(sheet_id, fc_id))

        view = lens.render(sdb, session, art_id)
        assert ">10</td>" in view.content
        assert ">20</td>" in view.content

    def test_render_multi_sheet(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Multi")
        art_id = sdb.create_node(session, art)

        for i in range(2):
            sheet = Sheet(
                meta=make_node_metadata(NodeType.SHEET),
                title=f"Sheet{i + 1}",
                rows=1,
                cols=1,
            )
            sid = sdb.create_node(session, sheet)
            sdb.create_edge(session, _contains(art_id, sid))
            cell = Cell(
                meta=make_node_metadata(NodeType.CELL), value=f"S{i + 1}", row=0, col=0,
            )
            cid = sdb.create_node(session, cell)
            sdb.create_edge(session, _contains(sid, cid))

        view = lens.render(sdb, session, art_id)
        # Should contain 2 tables
        assert view.content.count("<table") == 2

    def test_render_html_escaping(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Esc")
        art_id = sdb.create_node(session, art)

        sheet = Sheet(
            meta=make_node_metadata(NodeType.SHEET), title="S1", rows=1, cols=1,
        )
        sid = sdb.create_node(session, sheet)
        sdb.create_edge(session, _contains(art_id, sid))

        cell = Cell(
            meta=make_node_metadata(NodeType.CELL),
            value="<script>alert(1)</script>",
            row=0,
            col=0,
        )
        cid = sdb.create_node(session, cell)
        sdb.create_edge(session, _contains(sid, cid))

        view = lens.render(sdb, session, art_id)
        assert "<script>" not in view.content
        assert "&lt;script&gt;" in view.content


class TestGridLensActions:
    def test_set_cell_value(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        # Find first cell
        cells = sdb.get_children(session, sheet_id)
        first_cell = cells[0]

        action = SetCellValue(cell_id=first_cell.meta.id, value="Updated")
        lens.apply_action(sdb, session, art_id, action)

        node = sdb.get_node(session, first_cell.meta.id)
        assert isinstance(node, Cell)
        assert node.value == "Updated"

    def test_set_formula_cell_cached_value(self) -> None:
        sdb, session, lens = _setup()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="FC")
        art_id = sdb.create_node(session, art)
        sheet = Sheet(
            meta=make_node_metadata(NodeType.SHEET), title="S", rows=1, cols=1,
        )
        sid = sdb.create_node(session, sheet)
        sdb.create_edge(session, _contains(art_id, sid))
        fc = FormulaCell(
            meta=make_node_metadata(NodeType.FORMULA_CELL),
            formula="=SUM(A1:A5)",
            cached_value=10,
            row=0,
            col=0,
        )
        fc_id = sdb.create_node(session, fc)
        sdb.create_edge(session, _contains(sid, fc_id))

        action = SetCellValue(cell_id=fc_id, value=42)
        lens.apply_action(sdb, session, art_id, action)

        node = sdb.get_node(session, fc_id)
        assert isinstance(node, FormulaCell)
        assert node.cached_value == 42
        assert node.formula == "=SUM(A1:A5)"

    def test_insert_row(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        # Insert row at position 1 (between row 0 and row 1)
        action = InsertRow(sheet_id=sheet_id, position=1)
        lens.apply_action(sdb, session, art_id, action)

        sheet = sdb.get_node(session, sheet_id)
        assert isinstance(sheet, Sheet)
        assert sheet.rows == 3  # was 2, now 3

        cells = sdb.get_children(session, sheet_id)
        # Original row 1 cells should now be at row 2
        row2_cells = [c for c in cells if isinstance(c, Cell) and c.row == 2]
        assert len(row2_cells) == 3

        # New empty cells at row 1
        row1_cells = [c for c in cells if isinstance(c, Cell) and c.row == 1]
        assert len(row1_cells) == 3
        assert all(c.value is None for c in row1_cells)

    def test_insert_column(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        action = InsertColumn(sheet_id=sheet_id, position=1)
        lens.apply_action(sdb, session, art_id, action)

        sheet = sdb.get_node(session, sheet_id)
        assert isinstance(sheet, Sheet)
        assert sheet.cols == 4  # was 3, now 4

        cells = sdb.get_children(session, sheet_id)
        # Original col 1 cells ("B", 2) should now be at col 2
        col2_cells = [c for c in cells if isinstance(c, Cell) and c.col == 2]
        values = sorted([str(c.value) for c in col2_cells])
        assert "2" in values
        assert "B" in values

    def test_delete_row(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        action = DeleteRow(sheet_id=sheet_id, position=0)
        lens.apply_action(sdb, session, art_id, action)

        sheet = sdb.get_node(session, sheet_id)
        assert isinstance(sheet, Sheet)
        assert sheet.rows == 1

        cells = sdb.get_children(session, sheet_id)
        cell_objs = [c for c in cells if isinstance(c, Cell)]
        assert len(cell_objs) == 3
        # Remaining cells should be at row 0 (was row 1)
        assert all(c.row == 0 for c in cell_objs)

    def test_delete_column(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        action = DeleteColumn(sheet_id=sheet_id, position=2)
        lens.apply_action(sdb, session, art_id, action)

        sheet = sdb.get_node(session, sheet_id)
        assert isinstance(sheet, Sheet)
        assert sheet.cols == 2

        cells = sdb.get_children(session, sheet_id)
        cell_objs = [c for c in cells if isinstance(c, Cell)]
        # 2 rows * 2 cols = 4 cells
        assert len(cell_objs) == 4
        # No cell at col 2
        assert all(c.col < 2 for c in cell_objs)

    def test_rename_artifact(self) -> None:
        sdb, session, lens = _setup()
        art_id, _ = _make_spreadsheet(sdb, session)

        action = RenameArtifact(artifact_id=art_id, title="Budget 2026")
        lens.apply_action(sdb, session, art_id, action)

        art = sdb.get_node(session, art_id)
        assert isinstance(art, Artifact)
        assert art.title == "Budget 2026"

    def test_unsupported_action_raises(self) -> None:
        from uaf.app.lenses.actions import InsertText

        sdb, session, lens = _setup()
        art_id, _ = _make_spreadsheet(sdb, session)

        action = InsertText(parent_id=art_id, text="x", position=0)
        try:
            lens.apply_action(sdb, session, art_id, action)
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "InsertText" in str(e)


class TestParseCellValue:
    """Tests for the _parse_cell_value helper."""

    def test_empty_string_returns_none(self) -> None:
        from uaf.app.frontend.routes import _parse_cell_value

        assert _parse_cell_value("") is None

    def test_true_returns_bool(self) -> None:
        from uaf.app.frontend.routes import _parse_cell_value

        assert _parse_cell_value("true") is True
        assert _parse_cell_value("True") is True
        assert _parse_cell_value("TRUE") is True

    def test_false_returns_bool(self) -> None:
        from uaf.app.frontend.routes import _parse_cell_value

        assert _parse_cell_value("false") is False
        assert _parse_cell_value("False") is False

    def test_int_string(self) -> None:
        from uaf.app.frontend.routes import _parse_cell_value

        assert _parse_cell_value("42") == 42
        assert isinstance(_parse_cell_value("42"), int)

    def test_float_string(self) -> None:
        from uaf.app.frontend.routes import _parse_cell_value

        assert _parse_cell_value("3.14") == 3.14
        assert isinstance(_parse_cell_value("3.14"), float)

    def test_string_passthrough(self) -> None:
        from uaf.app.frontend.routes import _parse_cell_value

        assert _parse_cell_value("hello") == "hello"

    def test_negative_int(self) -> None:
        from uaf.app.frontend.routes import _parse_cell_value

        assert _parse_cell_value("-7") == -7
        assert isinstance(_parse_cell_value("-7"), int)


class TestSetCellBoolNone:
    """Verify bool/None roundtrip through GridLens."""

    def test_set_cell_bool_value(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        cells = sdb.get_children(session, sheet_id)
        first_cell = cells[0]

        action = SetCellValue(cell_id=first_cell.meta.id, value=True)
        lens.apply_action(sdb, session, art_id, action)

        node = sdb.get_node(session, first_cell.meta.id)
        assert isinstance(node, Cell)
        assert node.value is True

    def test_set_cell_none(self) -> None:
        sdb, session, lens = _setup()
        art_id, sheet_id = _make_spreadsheet(sdb, session)

        cells = sdb.get_children(session, sheet_id)
        first_cell = cells[0]

        action = SetCellValue(cell_id=first_cell.meta.id, value=None)
        lens.apply_action(sdb, session, art_id, action)

        node = sdb.get_node(session, first_cell.meta.id)
        assert isinstance(node, Cell)
        assert node.value is None

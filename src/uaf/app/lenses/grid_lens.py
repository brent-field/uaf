"""GridLens — spreadsheet rendering and editing lens."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from uaf.app.lenses import LensView
from uaf.app.lenses.actions import (
    DeleteColumn,
    DeleteNode,
    DeleteRow,
    InsertColumn,
    InsertRow,
    RenameArtifact,
    ReorderNodes,
    SetCellFormula,
    SetCellValue,
)
from uaf.core.edges import Edge, EdgeType
from uaf.core.formula import evaluate_formula
from uaf.core.node_id import EdgeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Cell,
    FormulaCell,
    NodeMetadata,
    NodeType,
    Sheet,
    make_node_metadata,
)

if TYPE_CHECKING:
    from uaf.app.lenses.actions import LensAction
    from uaf.core.node_id import NodeId
    from uaf.core.nodes import CellValue
    from uaf.security.secure_graph_db import SecureGraphDB, Session

_SUPPORTED = frozenset(
    {
        NodeType.ARTIFACT,
        NodeType.SHEET,
        NodeType.CELL,
        NodeType.FORMULA_CELL,
    }
)


class GridLens:
    """Renders a spreadsheet artifact as an HTML table."""

    @property
    def lens_type(self) -> str:
        return "grid"

    @property
    def supported_node_types(self) -> frozenset[NodeType]:
        return _SUPPORTED

    def render(self, db: SecureGraphDB, session: Session, artifact_id: NodeId) -> LensView:
        """Render the spreadsheet as HTML tables."""
        artifact = db.get_node(session, artifact_id)
        if artifact is None or not isinstance(artifact, Artifact):
            return LensView(
                lens_type="grid",
                artifact_id=artifact_id,
                title="(not found)",
                content="",
                content_type="text/html",
                node_count=0,
                rendered_at=utc_now(),
            )

        children = db.get_children(session, artifact_id)
        sheets = [c for c in children if isinstance(c, Sheet)]

        parts: list[str] = []
        node_count = 1  # artifact

        for sheet in sheets:
            html, count = self._render_sheet(sheet, db, session)
            parts.append(html)
            node_count += count

        content = "\n".join(parts)
        return LensView(
            lens_type="grid",
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
            case SetCellValue(cell_id=cell_id, value=value):
                self._set_cell_value(db, session, cell_id, value)
            case SetCellFormula(cell_id=cell_id, formula=formula, cached_value=cached):
                self._set_cell_formula(
                    db,
                    session,
                    cell_id,
                    formula,
                    cached,
                )
            case InsertRow(sheet_id=sheet_id, position=pos):
                self._insert_row(db, session, sheet_id, pos)
            case InsertColumn(sheet_id=sheet_id, position=pos):
                self._insert_column(db, session, sheet_id, pos)
            case DeleteRow(sheet_id=sheet_id, position=pos):
                self._delete_row(db, session, sheet_id, pos)
            case DeleteColumn(sheet_id=sheet_id, position=pos):
                self._delete_column(db, session, sheet_id, pos)
            case ReorderNodes(parent_id=parent_id, new_order=new_order):
                self._reorder(db, session, parent_id, new_order)
            case DeleteNode(node_id=node_id):
                db.delete_node(session, node_id)
            case RenameArtifact(artifact_id=aid, title=title):
                self._rename(db, session, aid, title)
            case _:
                msg = f"GridLens does not support action: {type(action).__name__}"
                raise ValueError(msg)

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _render_sheet(self, sheet: Sheet, db: SecureGraphDB, session: Session) -> tuple[str, int]:
        """Render a single sheet as an HTML table. Returns (html, node_count)."""
        sheet_id = sheet.meta.id
        cells = db.get_children(session, sheet_id)
        node_count = 1  # the sheet itself

        # Build grid
        max_row = sheet.rows - 1 if sheet.rows > 0 else -1
        max_col = sheet.cols - 1 if sheet.cols > 0 else -1

        # (row, col) -> (display_value, node_id, formula_or_none)
        grid: dict[tuple[int, int], tuple[str, str, str | None]] = {}

        for cell in cells:
            if isinstance(cell, Cell):
                val = str(cell.value) if cell.value is not None else ""
                grid[(cell.row, cell.col)] = (val, str(cell.meta.id), None)
                max_row = max(max_row, cell.row)
                max_col = max(max_col, cell.col)
                node_count += 1
            elif isinstance(cell, FormulaCell):
                val = str(cell.cached_value) if cell.cached_value is not None else ""
                grid[(cell.row, cell.col)] = (
                    val,
                    str(cell.meta.id),
                    cell.formula,
                )
                max_row = max(max_row, cell.row)
                max_col = max(max_col, cell.col)
                node_count += 1

        # Column letter headers (A, B, C, ...)
        col_headers = [f"    <th>{chr(ord('A') + c)}</th>" for c in range(max_col + 1)]
        thead = (
            "  <thead>\n  <tr>\n    <th></th>\n"
            + "\n".join(col_headers)
            + "\n  </tr>\n  </thead>"
        )

        rows_html: list[str] = []
        for r in range(max_row + 1):
            tds: list[str] = [f"    <th>{r + 1}</th>"]
            for c in range(max_col + 1):
                if (r, c) in grid:
                    val, nid, formula = grid[(r, c)]
                    formula_attr = f' data-formula="{escape(formula)}"' if formula else ""
                    tds.append(
                        f'    <td data-node-id="{nid}" '
                        f'data-row="{r}" data-col="{c}"'
                        f"{formula_attr}>"
                        f"{escape(val)}</td>"
                    )
                else:
                    tds.append(f'    <td data-row="{r}" data-col="{c}"></td>')
            rows_html.append("  <tr>\n" + "\n".join(tds) + "\n  </tr>")

        table_html = (
            f'<table data-sheet-id="{sheet_id}">\n'
            + thead
            + "\n"
            + "\n".join(rows_html)
            + "\n</tbody>\n</table>"
        )
        return table_html, node_count

    # ------------------------------------------------------------------
    # Action helpers
    # ------------------------------------------------------------------

    def _set_cell_formula(
        self,
        db: SecureGraphDB,
        session: Session,
        cell_id: NodeId,
        formula: str,
        cached_value: CellValue,
    ) -> None:
        """Set a formula on a cell, converting Cell -> FormulaCell if needed."""
        existing = db.get_node(session, cell_id)
        if existing is None:
            return
        if isinstance(existing, FormulaCell):
            updated_fc = FormulaCell(
                meta=existing.meta,
                formula=formula,
                cached_value=cached_value,
                row=existing.row,
                col=existing.col,
            )
            db.update_node(session, updated_fc)
        elif isinstance(existing, Cell):
            # Convert Cell -> FormulaCell (reuse same ID via metadata)
            fc = FormulaCell(
                meta=NodeMetadata(
                    id=existing.meta.id,
                    node_type=NodeType.FORMULA_CELL,
                    created_at=existing.meta.created_at,
                    updated_at=utc_now(),
                    owner=existing.meta.owner,
                    layout=existing.meta.layout,
                ),
                formula=formula,
                cached_value=cached_value,
                row=existing.row,
                col=existing.col,
            )
            db.update_node(session, fc)

    def recalculate_sheet(
        self,
        db: SecureGraphDB,
        session: Session,
        sheet_id: NodeId,
    ) -> None:
        """Re-evaluate all FormulaCell nodes in a sheet."""
        cells = db.get_children(session, sheet_id)

        # Build value lookup from all cells
        cell_values: dict[tuple[int, int], CellValue] = {}
        formula_cells: list[FormulaCell] = []
        for cell in cells:
            if isinstance(cell, Cell):
                cell_values[(cell.row, cell.col)] = cell.value
            elif isinstance(cell, FormulaCell):
                formula_cells.append(cell)
                cell_values[(cell.row, cell.col)] = cell.cached_value

        # Evaluate each formula cell
        for fc in formula_cells:

            def _getter(
                row: int,
                col: int,
                *,
                _vals: dict[tuple[int, int], CellValue] = cell_values,
            ) -> CellValue:
                return _vals.get((row, col))

            new_val = evaluate_formula(fc.formula, _getter)
            if new_val != fc.cached_value:
                updated = FormulaCell(
                    meta=fc.meta,
                    formula=fc.formula,
                    cached_value=new_val,
                    row=fc.row,
                    col=fc.col,
                )
                db.update_node(session, updated)
                # Update lookup so later formulas see the new value
                cell_values[(fc.row, fc.col)] = new_val

    def _set_cell_value(
        self, db: SecureGraphDB, session: Session, cell_id: NodeId, value: CellValue
    ) -> None:
        """Update a cell's value."""
        existing = db.get_node(session, cell_id)
        if existing is None:
            return
        if isinstance(existing, Cell):
            updated = Cell(
                meta=existing.meta,
                value=value,
                row=existing.row,
                col=existing.col,
            )
            db.update_node(session, updated)
        elif isinstance(existing, FormulaCell):
            updated_fc = FormulaCell(
                meta=existing.meta,
                formula=existing.formula,
                cached_value=value,
                row=existing.row,
                col=existing.col,
            )
            db.update_node(session, updated_fc)

    def _insert_row(
        self, db: SecureGraphDB, session: Session, sheet_id: NodeId, position: int
    ) -> None:
        """Insert a row at position, shifting existing cells down."""
        sheet = db.get_node(session, sheet_id)
        if sheet is None or not isinstance(sheet, Sheet):
            return

        cells = db.get_children(session, sheet_id)

        # Shift cells at or below position down by 1
        for cell in cells:
            if isinstance(cell, Cell) and cell.row >= position:
                updated = Cell(
                    meta=cell.meta,
                    value=cell.value,
                    row=cell.row + 1,
                    col=cell.col,
                )
                db.update_node(session, updated)
            elif isinstance(cell, FormulaCell) and cell.row >= position:
                updated_fc = FormulaCell(
                    meta=cell.meta,
                    formula=cell.formula,
                    cached_value=cell.cached_value,
                    row=cell.row + 1,
                    col=cell.col,
                )
                db.update_node(session, updated_fc)

        # Create new empty cells for the row
        for c in range(sheet.cols):
            new_cell = Cell(
                meta=make_node_metadata(NodeType.CELL),
                value=None,
                row=position,
                col=c,
            )
            cid = db.create_node(session, new_cell)
            edge = Edge(
                id=EdgeId.generate(),
                source=sheet_id,
                target=cid,
                edge_type=EdgeType.CONTAINS,
                created_at=utc_now(),
            )
            db.create_edge(session, edge)

        # Update sheet dimensions
        updated_sheet = Sheet(
            meta=sheet.meta,
            title=sheet.title,
            rows=sheet.rows + 1,
            cols=sheet.cols,
        )
        db.update_node(session, updated_sheet)

    def _insert_column(
        self, db: SecureGraphDB, session: Session, sheet_id: NodeId, position: int
    ) -> None:
        """Insert a column at position, shifting existing cells right."""
        sheet = db.get_node(session, sheet_id)
        if sheet is None or not isinstance(sheet, Sheet):
            return

        cells = db.get_children(session, sheet_id)

        # Shift cells at or right of position
        for cell in cells:
            if isinstance(cell, Cell) and cell.col >= position:
                updated = Cell(
                    meta=cell.meta,
                    value=cell.value,
                    row=cell.row,
                    col=cell.col + 1,
                )
                db.update_node(session, updated)
            elif isinstance(cell, FormulaCell) and cell.col >= position:
                updated_fc = FormulaCell(
                    meta=cell.meta,
                    formula=cell.formula,
                    cached_value=cell.cached_value,
                    row=cell.row,
                    col=cell.col + 1,
                )
                db.update_node(session, updated_fc)

        # Create new empty cells for the column
        for r in range(sheet.rows):
            new_cell = Cell(
                meta=make_node_metadata(NodeType.CELL),
                value=None,
                row=r,
                col=position,
            )
            cid = db.create_node(session, new_cell)
            edge = Edge(
                id=EdgeId.generate(),
                source=sheet_id,
                target=cid,
                edge_type=EdgeType.CONTAINS,
                created_at=utc_now(),
            )
            db.create_edge(session, edge)

        # Update sheet dimensions
        updated_sheet = Sheet(
            meta=sheet.meta,
            title=sheet.title,
            rows=sheet.rows,
            cols=sheet.cols + 1,
        )
        db.update_node(session, updated_sheet)

    def _delete_row(
        self, db: SecureGraphDB, session: Session, sheet_id: NodeId, position: int
    ) -> None:
        """Delete a row and shift cells up."""
        sheet = db.get_node(session, sheet_id)
        if sheet is None or not isinstance(sheet, Sheet):
            return

        cells = db.get_children(session, sheet_id)
        state = db._db._materializer.state

        for cell in cells:
            row = _cell_row(cell)
            if row is None:
                continue
            if row == position:
                # Delete this cell and its CONTAINS edge
                for eid, edge in list(state.edges.items()):
                    if edge.target == cell.meta.id and edge.edge_type == EdgeType.CONTAINS:
                        db.delete_edge(session, eid)
                db.delete_node(session, cell.meta.id)
            elif row > position:
                # Shift up
                if isinstance(cell, Cell):
                    updated = Cell(
                        meta=cell.meta,
                        value=cell.value,
                        row=row - 1,
                        col=cell.col,
                    )
                    db.update_node(session, updated)
                elif isinstance(cell, FormulaCell):
                    updated_fc = FormulaCell(
                        meta=cell.meta,
                        formula=cell.formula,
                        cached_value=cell.cached_value,
                        row=row - 1,
                        col=cell.col,
                    )
                    db.update_node(session, updated_fc)

        # Update sheet dimensions
        new_rows = max(sheet.rows - 1, 0)
        updated_sheet = Sheet(
            meta=sheet.meta,
            title=sheet.title,
            rows=new_rows,
            cols=sheet.cols,
        )
        db.update_node(session, updated_sheet)

    def _delete_column(
        self, db: SecureGraphDB, session: Session, sheet_id: NodeId, position: int
    ) -> None:
        """Delete a column and shift cells left."""
        sheet = db.get_node(session, sheet_id)
        if sheet is None or not isinstance(sheet, Sheet):
            return

        cells = db.get_children(session, sheet_id)
        state = db._db._materializer.state

        for cell in cells:
            col = _cell_col(cell)
            if col is None:
                continue
            if col == position:
                # Delete this cell and its CONTAINS edge
                for eid, edge in list(state.edges.items()):
                    if edge.target == cell.meta.id and edge.edge_type == EdgeType.CONTAINS:
                        db.delete_edge(session, eid)
                db.delete_node(session, cell.meta.id)
            elif col > position:
                # Shift left
                if isinstance(cell, Cell):
                    updated = Cell(
                        meta=cell.meta,
                        value=cell.value,
                        row=cell.row,
                        col=col - 1,
                    )
                    db.update_node(session, updated)
                elif isinstance(cell, FormulaCell):
                    updated_fc = FormulaCell(
                        meta=cell.meta,
                        formula=cell.formula,
                        cached_value=cell.cached_value,
                        row=cell.row,
                        col=col - 1,
                    )
                    db.update_node(session, updated_fc)

        # Update sheet dimensions
        new_cols = max(sheet.cols - 1, 0)
        updated_sheet = Sheet(
            meta=sheet.meta,
            title=sheet.title,
            rows=sheet.rows,
            cols=new_cols,
        )
        db.update_node(session, updated_sheet)

    def _reorder(
        self,
        db: SecureGraphDB,
        session: Session,
        parent_id: NodeId,
        new_order: tuple[NodeId, ...],
    ) -> None:
        """Reorder children of a parent node."""
        from uaf.core.operations import ReorderChildren

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


def _cell_row(cell: object) -> int | None:
    """Extract row from a Cell or FormulaCell."""
    if isinstance(cell, Cell):
        return cell.row
    if isinstance(cell, FormulaCell):
        return cell.row
    return None


def _cell_col(cell: object) -> int | None:
    """Extract col from a Cell or FormulaCell."""
    if isinstance(cell, Cell):
        return cell.col
    if isinstance(cell, FormulaCell):
        return cell.col
    return None

"""CSV import/export/compare using the csv stdlib."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

from uaf.app.formats import ComparisonResult
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Cell,
    NodeType,
    Sheet,
    make_node_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.db.graph_db import GraphDB


class CsvHandler:
    """Import/export CSV files via the UAF graph."""

    def import_file(self, path: Path, db: GraphDB) -> NodeId:
        """Parse a CSV file into UAF Sheet/Cell nodes."""
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Strip trailing empty rows
        while rows and all(cell == "" for cell in rows[-1]):
            rows.pop()

        num_rows = len(rows)
        num_cols = max((len(row) for row in rows), default=0)

        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=path.stem)
        art_id = db.create_node(art)

        sheet = Sheet(
            meta=make_node_metadata(NodeType.SHEET), title="Sheet1", rows=num_rows, cols=num_cols,
        )
        sheet_id = db.create_node(sheet)
        db.create_edge(_contains(art_id, sheet_id))

        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                cell = Cell(
                    meta=make_node_metadata(NodeType.CELL), value=value, row=r, col=c,
                )
                cell_id = db.create_node(cell)
                db.create_edge(_contains(sheet_id, cell_id))

        return art_id

    def export_file(self, db: GraphDB, root_id: NodeId, path: Path) -> None:
        """Export a UAF artifact as a CSV file."""
        children = db.get_children(root_id)
        sheet = next((c for c in children if isinstance(c, Sheet)), None)
        if sheet is None:
            path.write_text("", encoding="utf-8")
            return

        sheet_id = sheet.meta.id
        cells = db.get_children(sheet_id)

        # Build grid
        num_rows = sheet.rows
        num_cols = sheet.cols
        grid: list[list[str]] = [[""] * num_cols for _ in range(num_rows)]

        for cell in cells:
            if isinstance(cell, Cell) and cell.row < num_rows and cell.col < num_cols:
                grid[cell.row][cell.col] = str(cell.value) if cell.value is not None else ""

        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            for row in grid:
                writer.writerow(row)


class CsvComparator:
    """Compare two CSV files for data equivalence."""

    def compare(self, original: Path, rebuilt: Path) -> ComparisonResult:
        """Compare CSV files by cell values, ignoring quoting style and trailing newlines."""
        orig_rows = _read_csv(original)
        rebuilt_rows = _read_csv(rebuilt)

        differences: list[str] = []
        ignored: list[str] = []

        # Strip trailing empty rows from both
        while orig_rows and all(cell == "" for cell in orig_rows[-1]):
            orig_rows.pop()
        while rebuilt_rows and all(cell == "" for cell in rebuilt_rows[-1]):
            rebuilt_rows.pop()

        if len(orig_rows) != len(rebuilt_rows):
            differences.append(
                f"Row count: {len(orig_rows)} != {len(rebuilt_rows)}"
            )

        max_rows = max(len(orig_rows), len(rebuilt_rows))
        total_cells = 0
        matching_cells = 0

        for r in range(max_rows):
            orig_row = orig_rows[r] if r < len(orig_rows) else []
            rebuilt_row = rebuilt_rows[r] if r < len(rebuilt_rows) else []
            max_cols = max(len(orig_row), len(rebuilt_row))

            for c in range(max_cols):
                total_cells += 1
                orig_val = orig_row[c] if c < len(orig_row) else ""
                rebuilt_val = rebuilt_row[c] if c < len(rebuilt_row) else ""
                if orig_val == rebuilt_val:
                    matching_cells += 1
                else:
                    differences.append(
                        f"Cell ({r},{c}): {orig_val!r} != {rebuilt_val!r}"
                    )

        score = matching_cells / total_cells if total_cells > 0 else 1.0
        is_eq = len(differences) == 0

        # Check for quoting style difference
        orig_raw = original.read_text(encoding="utf-8")
        rebuilt_raw = rebuilt.read_text(encoding="utf-8")
        if orig_raw != rebuilt_raw and is_eq:
            ignored.append("quoting style or trailing newline differs")

        return ComparisonResult(
            is_equivalent=is_eq,
            differences=differences,
            ignored=ignored,
            similarity_score=score,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contains(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))

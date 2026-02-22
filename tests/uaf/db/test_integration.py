"""Integration scenario tests for the full database layer."""

from __future__ import annotations

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Cell,
    FormulaCell,
    Heading,
    NodeType,
    Paragraph,
    Sheet,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB


def _contains(source: object, target: object) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


def _reference(source: object, target: object) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        edge_type=EdgeType.REFERENCES,
        created_at=utc_now(),
    )


class TestDocumentAuthoring:
    """Scenario 1: Create artifact -> add heading + paragraphs -> reorder -> query tree."""

    def test_full_document_workflow(self) -> None:
        db = GraphDB()

        # Create artifact
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Quarterly Report")
        art_id = db.create_node(art)

        # Add heading and paragraphs
        h1 = Heading(meta=make_node_metadata(NodeType.HEADING), text="Summary", level=1)
        p1 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Revenue grew 15%.")
        p2 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Costs decreased 5%.")

        h1_id = db.create_node(h1)
        p1_id = db.create_node(p1)
        p2_id = db.create_node(p2)

        db.create_edge(_contains(art_id, h1_id))
        db.create_edge(_contains(art_id, p1_id))
        db.create_edge(_contains(art_id, p2_id))

        # Verify tree
        children = db.get_children(art_id)
        assert len(children) == 3
        assert children[0] == h1
        assert children[1] == p1
        assert children[2] == p2

        # Reorder: move p2 before p1
        from uaf.core.operations import ReorderChildren

        reorder_op = ReorderChildren(
            parent_id=art_id,
            new_order=(h1_id, p2_id, p1_id),
            parent_ops=(),
            timestamp=utc_now(),
        )
        db.apply(reorder_op)

        children_after = db.get_children(art_id)
        assert children_after[0] == h1
        assert children_after[1] == p2
        assert children_after[2] == p1

        # Query by type
        headings = db.find_by_type(NodeType.HEADING)
        assert len(headings) == 1
        assert headings[0].text == "Summary"

        paragraphs = db.find_by_type(NodeType.PARAGRAPH)
        assert len(paragraphs) == 2


class TestSpreadsheet:
    """Scenario 2: Create sheet artifact -> add cells + formulas -> query by type."""

    def test_spreadsheet_workflow(self) -> None:
        db = GraphDB()

        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Budget")
        art_id = db.create_node(art)

        sheet = Sheet(meta=make_node_metadata(NodeType.SHEET), title="Q1", rows=10, cols=5)
        sheet_id = db.create_node(sheet)
        db.create_edge(_contains(art_id, sheet_id))

        c1 = Cell(meta=make_node_metadata(NodeType.CELL), value=100, row=0, col=0)
        c2 = Cell(meta=make_node_metadata(NodeType.CELL), value=200, row=1, col=0)
        fc = FormulaCell(
            meta=make_node_metadata(NodeType.FORMULA_CELL),
            formula="=SUM(A1:A2)", cached_value=300.0, row=2, col=0,
        )

        c1_id = db.create_node(c1)
        c2_id = db.create_node(c2)
        fc_id = db.create_node(fc)

        db.create_edge(_contains(sheet_id, c1_id))
        db.create_edge(_contains(sheet_id, c2_id))
        db.create_edge(_contains(sheet_id, fc_id))

        # Query by type
        cells = db.find_by_type(NodeType.CELL)
        assert len(cells) == 2

        formulas = db.find_by_type(NodeType.FORMULA_CELL)
        assert len(formulas) == 1
        assert formulas[0].formula == "=SUM(A1:A2)"

        # Verify tree structure
        sheet_children = db.get_children(sheet_id)
        assert len(sheet_children) == 3


class TestTransclusion:
    """Scenario 3: Shared paragraph referenced from two artifacts."""

    def test_transclusion(self) -> None:
        db = GraphDB()

        art1 = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Report")
        art2 = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Board Deck")
        shared_p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Key finding.")

        art1_id = db.create_node(art1)
        art2_id = db.create_node(art2)
        p_id = db.create_node(shared_p)

        # art1 contains the paragraph
        db.create_edge(_contains(art1_id, p_id))
        # art2 references (transclude) the same paragraph
        db.create_edge(_reference(art2_id, p_id))

        # p appears as a child of art1
        assert db.get_children(art1_id) == [shared_p]

        # art2 references p
        refs = db.get_references_to(p_id)
        assert len(refs) == 1
        assert refs[0] == art2

        # Update the shared paragraph — both artifacts see the update
        updated_p = Paragraph(meta=shared_p.meta, text="Updated key finding.")
        db.update_node(updated_p)

        assert db.get_node(p_id).text == "Updated key finding."
        assert db.get_children(art1_id)[0].text == "Updated key finding."


class TestHistory:
    """Scenario 4: Create -> update 5x -> verify full operation history."""

    def test_history_accumulates(self) -> None:
        db = GraphDB()

        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="V0")
        art_id = db.create_node(art)

        for i in range(1, 6):
            updated = Artifact(meta=art.meta, title=f"V{i}")
            db.update_node(updated)

        history = db.get_history(art_id)
        assert len(history) == 6  # 1 create + 5 updates


class TestOrphanSemantics:
    """Scenario 5: Delete parent -> children remain (no cascade)."""

    def test_no_cascade_delete(self) -> None:
        db = GraphDB()

        parent = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Parent")
        child = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Child")

        parent_id = db.create_node(parent)
        child_id = db.create_node(child)
        db.create_edge(_contains(parent_id, child_id))

        # Delete parent
        db.delete_node(parent_id)

        # Parent is gone, child survives
        assert db.get_node(parent_id) is None
        assert db.get_node(child_id) == child

        # Child is orphaned but still exists
        assert db.count_nodes() == 1


class TestOwnership:
    """Scenario 6: Create nodes with owners -> query by owner via AVET index."""

    def test_query_by_owner(self) -> None:
        db = GraphDB()

        alice_art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, owner="alice"), title="Alice's Doc",
        )
        bob_art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, owner="bob"), title="Bob's Doc",
        )
        alice_p = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH, owner="alice"), text="Alice's para",
        )

        db.create_node(alice_art)
        db.create_node(bob_art)
        db.create_node(alice_p)

        alice_nodes = db.find_by_attribute("owner", "alice")
        assert len(alice_nodes) == 2

        bob_nodes = db.find_by_attribute("owner", "bob")
        assert len(bob_nodes) == 1
        assert bob_nodes[0] == bob_art

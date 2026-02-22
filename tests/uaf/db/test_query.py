"""Tests for the query engine using a pre-populated graph fixture."""

from __future__ import annotations

import pytest

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Heading,
    NodeType,
    Paragraph,
    Task,
    make_node_metadata,
)
from uaf.core.operations import CreateEdge, CreateNode
from uaf.db.eavt import Datom, EAVTIndex
from uaf.db.materializer import StateMaterializer
from uaf.db.operation_log import LogEntry
from uaf.db.query import QueryEngine

# ---------------------------------------------------------------------------
# Fixture: pre-populated graph
# ---------------------------------------------------------------------------


def _make_entry(op: object) -> LogEntry:
    from uaf.core.operations import compute_operation_id

    op_id = compute_operation_id(op)  # type: ignore[arg-type]
    return LogEntry(operation_id=op_id, operation=op)  # type: ignore[arg-type]


def _add_datoms(index: EAVTIndex, node: object, tx: str) -> None:
    """Extract datoms from a node and add to index."""
    meta = node.meta  # type: ignore[union-attr]
    entity = str(meta.id.value)
    index.add(Datom(entity=entity, attribute="node_type", value=meta.node_type.value, tx=tx))
    if hasattr(node, "title"):
        index.add(Datom(entity=entity, attribute="title", value=str(node.title), tx=tx))  # type: ignore[union-attr]
    if hasattr(node, "text"):
        index.add(Datom(entity=entity, attribute="text", value=str(node.text), tx=tx))  # type: ignore[union-attr]
    if meta.owner is not None:
        index.add(Datom(entity=entity, attribute="owner", value=meta.owner, tx=tx))


@pytest.fixture
def graph() -> tuple[QueryEngine, dict[str, object]]:
    """Build a pre-populated graph with an artifact, heading, two paragraphs, and a task."""
    mat = StateMaterializer()
    index = EAVTIndex()

    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT, owner="alice"), title="Report")
    h1 = Heading(meta=make_node_metadata(NodeType.HEADING), text="Summary", level=1)
    p1 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Revenue grew 15%.")
    p2 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Costs decreased 5%.")
    task = Task(meta=make_node_metadata(NodeType.TASK), title="Review report")

    nodes = {"art": art, "h1": h1, "p1": p1, "p2": p2, "task": task}

    # Create nodes
    for _name, node in nodes.items():
        entry = _make_entry(CreateNode(node=node, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry)
        _add_datoms(index, node, str(entry.operation_id))

    # Create CONTAINS edges: art -> h1, art -> p1, art -> p2
    edges = [
        Edge(id=EdgeId.generate(), source=art.meta.id, target=h1.meta.id,
             edge_type=EdgeType.CONTAINS, created_at=utc_now()),
        Edge(id=EdgeId.generate(), source=art.meta.id, target=p1.meta.id,
             edge_type=EdgeType.CONTAINS, created_at=utc_now()),
        Edge(id=EdgeId.generate(), source=art.meta.id, target=p2.meta.id,
             edge_type=EdgeType.CONTAINS, created_at=utc_now()),
    ]
    for edge in edges:
        entry = _make_entry(CreateEdge(edge=edge, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry)

    # Create a REFERENCES edge from task to p1
    ref_edge = Edge(
        id=EdgeId.generate(), source=task.meta.id, target=p1.meta.id,
        edge_type=EdgeType.REFERENCES, created_at=utc_now(),
    )
    entry = _make_entry(CreateEdge(edge=ref_edge, parent_ops=(), timestamp=utc_now()))
    mat.apply(entry)

    engine = QueryEngine(mat.state, index)
    return engine, nodes  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetNode:
    def test_existing(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, nodes = graph
        art = nodes["art"]
        result = engine.get_node(art.meta.id)  # type: ignore[union-attr]
        assert result == art

    def test_missing(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, _ = graph
        assert engine.get_node(NodeId.generate()) is None


class TestGetChildren:
    def test_returns_children_in_order(
        self, graph: tuple[QueryEngine, dict[str, object]]
    ) -> None:
        engine, nodes = graph
        art = nodes["art"]
        children = engine.get_children(art.meta.id)  # type: ignore[union-attr]
        assert len(children) == 3
        assert children[0] == nodes["h1"]
        assert children[1] == nodes["p1"]
        assert children[2] == nodes["p2"]

    def test_no_children(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, nodes = graph
        h1 = nodes["h1"]
        assert engine.get_children(h1.meta.id) == []  # type: ignore[union-attr]


class TestGetParent:
    def test_returns_parent(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, nodes = graph
        h1 = nodes["h1"]
        parent = engine.get_parent(h1.meta.id)  # type: ignore[union-attr]
        assert parent == nodes["art"]

    def test_root_has_no_parent(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, nodes = graph
        task = nodes["task"]
        assert engine.get_parent(task.meta.id) is None  # type: ignore[union-attr]


class TestGetReferencesTo:
    def test_returns_referencing_nodes(
        self, graph: tuple[QueryEngine, dict[str, object]]
    ) -> None:
        engine, nodes = graph
        p1 = nodes["p1"]
        refs = engine.get_references_to(p1.meta.id)  # type: ignore[union-attr]
        assert len(refs) == 1
        assert refs[0] == nodes["task"]

    def test_no_references(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, nodes = graph
        h1 = nodes["h1"]
        assert engine.get_references_to(h1.meta.id) == []  # type: ignore[union-attr]


class TestFindByType:
    def test_find_paragraphs(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, _ = graph
        results = engine.find_by_type(NodeType.PARAGRAPH)
        assert len(results) == 2

    def test_find_tasks(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, _ = graph
        results = engine.find_by_type(NodeType.TASK)
        assert len(results) == 1
        assert results[0].title == "Review report"  # type: ignore[union-attr]

    def test_find_nonexistent_type(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, _ = graph
        assert engine.find_by_type(NodeType.SHEET) == []


class TestFindByAttribute:
    def test_find_by_owner(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, nodes = graph
        results = engine.find_by_attribute("owner", "alice")
        assert len(results) == 1
        assert results[0] == nodes["art"]


class TestGetEdgesFrom:
    def test_returns_edges(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, nodes = graph
        art = nodes["art"]
        edges = engine.get_edges_from(art.meta.id)  # type: ignore[union-attr]
        assert len(edges) == 3
        assert all(e.edge_type == EdgeType.CONTAINS for e in edges)


class TestCounts:
    def test_count_nodes(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, _ = graph
        assert engine.count_nodes() == 5

    def test_count_edges(self, graph: tuple[QueryEngine, dict[str, object]]) -> None:
        engine, _ = graph
        assert engine.count_edges() == 4  # 3 CONTAINS + 1 REFERENCES

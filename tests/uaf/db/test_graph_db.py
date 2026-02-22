"""End-to-end tests for the GraphDB facade."""

from __future__ import annotations

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Heading,
    NodeType,
    Paragraph,
    make_node_metadata,
)
from uaf.db.graph_db import GraphDB


def _art(title: str = "Doc") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _para(text: str = "Hello") -> Paragraph:
    return Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)


def _heading(text: str = "H1", level: int = 1) -> Heading:
    return Heading(meta=make_node_metadata(NodeType.HEADING), text=text, level=level)


def _contains(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(), source=source, target=target,
        edge_type=EdgeType.CONTAINS, created_at=utc_now(),
    )


def _reference(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(), source=source, target=target,
        edge_type=EdgeType.REFERENCES, created_at=utc_now(),
    )


class TestCreateAndGet:
    def test_create_and_get_node(self) -> None:
        db = GraphDB()
        art = _art("Report")
        art_id = db.create_node(art)
        assert db.get_node(art_id) == art

    def test_get_missing_returns_none(self) -> None:
        db = GraphDB()
        assert db.get_node(NodeId.generate()) is None


class TestUpdateNode:
    def test_update_replaces_data(self) -> None:
        db = GraphDB()
        art = _art("V1")
        art_id = db.create_node(art)

        updated = Artifact(meta=art.meta, title="V2")
        db.update_node(updated)

        result = db.get_node(art_id)
        assert result is not None
        assert result.title == "V2"


class TestDeleteNode:
    def test_delete_removes_node(self) -> None:
        db = GraphDB()
        art = _art()
        art_id = db.create_node(art)
        db.delete_node(art_id)
        assert db.get_node(art_id) is None


class TestEdges:
    def test_create_edge_and_get_children(self) -> None:
        db = GraphDB()
        art = _art()
        p = _para()
        art_id = db.create_node(art)
        p_id = db.create_node(p)
        db.create_edge(_contains(art_id, p_id))

        children = db.get_children(art_id)
        assert len(children) == 1
        assert children[0] == p

    def test_delete_edge(self) -> None:
        db = GraphDB()
        art = _art()
        p = _para()
        art_id = db.create_node(art)
        p_id = db.create_node(p)
        edge = _contains(art_id, p_id)
        db.create_edge(edge)
        assert db.get_children(art_id) == [p]

        db.delete_edge(edge.id)
        assert db.get_children(art_id) == []


class TestGetParent:
    def test_returns_parent(self) -> None:
        db = GraphDB()
        art = _art()
        h = _heading()
        art_id = db.create_node(art)
        h_id = db.create_node(h)
        db.create_edge(_contains(art_id, h_id))

        assert db.get_parent(h_id) == art


class TestReferencesTo:
    def test_returns_referencing_nodes(self) -> None:
        db = GraphDB()
        art1 = _art("Doc1")
        art2 = _art("Doc2")
        p = _para("Shared paragraph")

        art1_id = db.create_node(art1)
        art2_id = db.create_node(art2)
        p_id = db.create_node(p)

        db.create_edge(_contains(art1_id, p_id))
        db.create_edge(_reference(art2_id, p_id))

        refs = db.get_references_to(p_id)
        assert len(refs) == 1
        assert refs[0] == art2


class TestFindByType:
    def test_find_paragraphs(self) -> None:
        db = GraphDB()
        db.create_node(_art())
        db.create_node(_para("P1"))
        db.create_node(_para("P2"))

        results = db.find_by_type(NodeType.PARAGRAPH)
        assert len(results) == 2

    def test_find_nonexistent_type(self) -> None:
        db = GraphDB()
        db.create_node(_art())
        assert db.find_by_type(NodeType.SHEET) == []


class TestFindByAttribute:
    def test_find_by_owner(self) -> None:
        db = GraphDB()
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, owner="alice"), title="Doc",
        )
        db.create_node(art)
        db.create_node(_para())

        results = db.find_by_attribute("owner", "alice")
        assert len(results) == 1
        assert results[0] == art


class TestGetHistory:
    def test_history_tracks_mutations(self) -> None:
        db = GraphDB()
        art = _art("V1")
        art_id = db.create_node(art)

        updated = Artifact(meta=art.meta, title="V2")
        db.update_node(updated)

        history = db.get_history(art_id)
        assert len(history) == 2


class TestDescendants:
    def test_recursive_descendants(self) -> None:
        db = GraphDB()
        art = _art()
        h = _heading()
        p = _para()

        art_id = db.create_node(art)
        h_id = db.create_node(h)
        p_id = db.create_node(p)
        db.create_edge(_contains(art_id, h_id))
        db.create_edge(_contains(art_id, p_id))

        desc = db.descendants(art_id)
        assert art_id in desc
        assert h_id in desc
        assert p_id in desc
        assert len(desc) == 3


class TestBlobStorage:
    def test_store_and_retrieve(self) -> None:
        db = GraphDB()
        data = b"hello world"
        bid = db.store_blob(data)
        assert db.get_blob(bid) == data

    def test_missing_blob_returns_none(self) -> None:
        from uaf.core.node_id import BlobId

        db = GraphDB()
        assert db.get_blob(BlobId(hex_digest="f" * 64)) is None

    def test_deduplication(self) -> None:
        db = GraphDB()
        data = b"same data"
        bid1 = db.store_blob(data)
        bid2 = db.store_blob(data)
        assert bid1 == bid2


class TestCounts:
    def test_counts(self) -> None:
        db = GraphDB()
        art = _art()
        p = _para()
        art_id = db.create_node(art)
        p_id = db.create_node(p)
        db.create_edge(_contains(art_id, p_id))

        assert db.count_nodes() == 2
        assert db.count_edges() == 1

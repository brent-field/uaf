"""Tests for the Edge model and EdgeType."""

import pytest

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now


class TestEdgeType:
    def test_all_types_exist(self) -> None:
        expected = {
            "CONTAINS",
            "REFERENCES",
            "DEPENDS_ON",
            "COMPLIES_WITH",
            "FOLLOWS",
            "LINKED_TO",
            "OWNED_BY",
            "GRANTS_ROLE",
        }
        actual = {t.name for t in EdgeType}
        assert actual == expected

    def test_values_are_lowercase(self) -> None:
        for t in EdgeType:
            assert t.value == t.name.lower()


class TestEdge:
    def _make_edge(
        self,
        edge_type: EdgeType = EdgeType.CONTAINS,
        properties: tuple[tuple[str, str | int | float | bool], ...] = (),
    ) -> Edge:
        return Edge(
            id=EdgeId.generate(),
            source=NodeId.generate(),
            target=NodeId.generate(),
            edge_type=edge_type,
            created_at=utc_now(),
            properties=properties,
        )

    def test_construction(self) -> None:
        edge = self._make_edge()
        assert isinstance(edge.id, EdgeId)
        assert isinstance(edge.source, NodeId)
        assert isinstance(edge.target, NodeId)

    def test_edge_type(self) -> None:
        edge = self._make_edge(edge_type=EdgeType.REFERENCES)
        assert edge.edge_type == EdgeType.REFERENCES

    def test_empty_properties(self) -> None:
        edge = self._make_edge()
        assert edge.properties == ()

    def test_with_properties(self) -> None:
        props: tuple[tuple[str, str | int | float | bool], ...] = (
            ("weight", 1.5),
            ("label", "strong"),
        )
        edge = self._make_edge(properties=props)
        assert len(edge.properties) == 2
        assert edge.properties[0] == ("weight", 1.5)

    def test_is_frozen(self) -> None:
        edge = self._make_edge()
        with pytest.raises(AttributeError):
            edge.edge_type = EdgeType.FOLLOWS  # type: ignore[misc]

    def test_is_hashable(self) -> None:
        edge = self._make_edge()
        s = {edge}
        assert edge in s

    def test_equality(self) -> None:
        eid = EdgeId.generate()
        src = NodeId.generate()
        tgt = NodeId.generate()
        now = utc_now()
        e1 = Edge(id=eid, source=src, target=tgt, edge_type=EdgeType.CONTAINS, created_at=now)
        e2 = Edge(id=eid, source=src, target=tgt, edge_type=EdgeType.CONTAINS, created_at=now)
        assert e1 == e2

    def test_different_ids_not_equal(self) -> None:
        src = NodeId.generate()
        tgt = NodeId.generate()
        now = utc_now()
        e1 = Edge(
            id=EdgeId.generate(),
            source=src,
            target=tgt,
            edge_type=EdgeType.CONTAINS,
            created_at=now,
        )
        e2 = Edge(
            id=EdgeId.generate(),
            source=src,
            target=tgt,
            edge_type=EdgeType.CONTAINS,
            created_at=now,
        )
        assert e1 != e2

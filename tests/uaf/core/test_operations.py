"""Tests for operation types and their serialization."""

from __future__ import annotations

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, OperationId, utc_now
from uaf.core.nodes import Artifact, Heading, NodeType, Paragraph, make_node_metadata
from uaf.core.operations import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    MoveNode,
    ReorderChildren,
    UpdateNode,
    compute_operation_id,
    operation_from_dict,
    operation_to_dict,
)


def _roundtrip_op(op: object) -> object:
    d = operation_to_dict(op)  # type: ignore[arg-type]
    return operation_from_dict(d)


VALID_HEX = "a" * 64


class TestCreateNode:
    def test_roundtrip(self) -> None:
        node = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Doc")
        op = CreateNode(node=node, parent_ops=(), timestamp=utc_now())
        assert _roundtrip_op(op) == op

    def test_with_parent_ops(self) -> None:
        node = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="P")
        pid = OperationId(hex_digest=VALID_HEX)
        op = CreateNode(node=node, parent_ops=(pid,), timestamp=utc_now())
        result = _roundtrip_op(op)
        assert result == op

    def test_with_principal(self) -> None:
        node = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="T")
        op = CreateNode(node=node, parent_ops=(), timestamp=utc_now(), principal_id="alice")
        result = _roundtrip_op(op)
        assert result == op


class TestUpdateNode:
    def test_roundtrip(self) -> None:
        node = Heading(meta=make_node_metadata(NodeType.HEADING), text="H", level=2)
        op = UpdateNode(node=node, parent_ops=(), timestamp=utc_now())
        assert _roundtrip_op(op) == op


class TestDeleteNode:
    def test_roundtrip(self) -> None:
        nid = NodeId.generate()
        op = DeleteNode(node_id=nid, parent_ops=(), timestamp=utc_now())
        assert _roundtrip_op(op) == op


class TestCreateEdge:
    def test_roundtrip(self) -> None:
        edge = Edge(
            id=EdgeId.generate(),
            source=NodeId.generate(),
            target=NodeId.generate(),
            edge_type=EdgeType.CONTAINS,
            created_at=utc_now(),
        )
        op = CreateEdge(edge=edge, parent_ops=(), timestamp=utc_now())
        assert _roundtrip_op(op) == op


class TestDeleteEdge:
    def test_roundtrip(self) -> None:
        eid = EdgeId.generate()
        op = DeleteEdge(edge_id=eid, parent_ops=(), timestamp=utc_now())
        assert _roundtrip_op(op) == op


class TestMoveNode:
    def test_roundtrip(self) -> None:
        op = MoveNode(
            node_id=NodeId.generate(),
            new_parent_id=NodeId.generate(),
            parent_ops=(),
            timestamp=utc_now(),
        )
        assert _roundtrip_op(op) == op


class TestReorderChildren:
    def test_roundtrip(self) -> None:
        ids = (NodeId.generate(), NodeId.generate(), NodeId.generate())
        op = ReorderChildren(
            parent_id=NodeId.generate(),
            new_order=ids,
            parent_ops=(),
            timestamp=utc_now(),
        )
        assert _roundtrip_op(op) == op


class TestComputeOperationId:
    def test_deterministic(self) -> None:
        node = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="T")
        op = CreateNode(node=node, parent_ops=(), timestamp=utc_now())
        h1 = compute_operation_id(op)
        h2 = compute_operation_id(op)
        assert h1 == h2

    def test_different_ops_different_ids(self) -> None:
        now = utc_now()
        op1 = CreateNode(
            node=Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="A"),
            parent_ops=(),
            timestamp=now,
        )
        op2 = CreateNode(
            node=Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="B"),
            parent_ops=(),
            timestamp=now,
        )
        assert compute_operation_id(op1) != compute_operation_id(op2)

    def test_returns_operation_id(self) -> None:
        node = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="T")
        op = CreateNode(node=node, parent_ops=(), timestamp=utc_now())
        result = compute_operation_id(op)
        assert isinstance(result, OperationId)

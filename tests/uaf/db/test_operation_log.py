"""Tests for the append-only operation log."""

from __future__ import annotations

import pytest

from uaf.core.errors import DuplicateOperationError, InvalidParentError
from uaf.core.node_id import utc_now
from uaf.core.nodes import Artifact, Heading, NodeType, Paragraph, make_node_metadata
from uaf.core.operations import CreateNode
from uaf.db.operation_log import LogEntry, OperationLog


def _make_create(title: str = "Doc") -> CreateNode:
    return CreateNode(
        node=Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title),
        parent_ops=(),
        timestamp=utc_now(),
    )


class TestAppend:
    def test_append_returns_operation_id(self) -> None:
        log = OperationLog()
        op_id = log.append(_make_create())
        assert op_id is not None

    def test_append_increments_length(self) -> None:
        log = OperationLog()
        assert len(log) == 0
        log.append(_make_create())
        assert len(log) == 1

    def test_append_chain(self) -> None:
        log = OperationLog()
        id1 = log.append(_make_create("A"))
        op2 = CreateNode(
            node=Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="P1"),
            parent_ops=(id1,),
            timestamp=utc_now(),
        )
        id2 = log.append(op2)
        assert id1 != id2
        assert len(log) == 2

    def test_duplicate_raises(self) -> None:
        log = OperationLog()
        op = _make_create()
        log.append(op)
        with pytest.raises(DuplicateOperationError):
            log.append(op)

    def test_invalid_parent_raises(self) -> None:
        from uaf.core.node_id import OperationId

        log = OperationLog()
        fake_parent = OperationId(hex_digest="b" * 64)
        op = CreateNode(
            node=Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="X"),
            parent_ops=(fake_parent,),
            timestamp=utc_now(),
        )
        with pytest.raises(InvalidParentError):
            log.append(op)


class TestGet:
    def test_get_existing(self) -> None:
        log = OperationLog()
        op = _make_create()
        op_id = log.append(op)
        entry = log.get(op_id)
        assert entry is not None
        assert entry.operation == op

    def test_get_missing_returns_none(self) -> None:
        from uaf.core.node_id import OperationId

        log = OperationLog()
        assert log.get(OperationId(hex_digest="c" * 64)) is None


class TestHeadIds:
    def test_genesis_is_head(self) -> None:
        log = OperationLog()
        id1 = log.append(_make_create())
        assert id1 in log.head_ids

    def test_parent_is_not_head(self) -> None:
        log = OperationLog()
        id1 = log.append(_make_create("A"))
        op2 = CreateNode(
            node=Heading(meta=make_node_metadata(NodeType.HEADING), text="H", level=1),
            parent_ops=(id1,),
            timestamp=utc_now(),
        )
        id2 = log.append(op2)
        heads = log.head_ids
        assert id1 not in heads
        assert id2 in heads

    def test_dag_fork_two_heads(self) -> None:
        log = OperationLog()
        id1 = log.append(_make_create("A"))
        op2 = CreateNode(
            node=Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="P1"),
            parent_ops=(id1,),
            timestamp=utc_now(),
        )
        id2 = log.append(op2)
        op3 = CreateNode(
            node=Heading(meta=make_node_metadata(NodeType.HEADING), text="H1", level=1),
            parent_ops=(id1,),
            timestamp=utc_now(),
        )
        id3 = log.append(op3)
        heads = log.head_ids
        assert id1 not in heads
        assert id2 in heads
        assert id3 in heads
        assert len(heads) == 2


class TestEntriesSince:
    def test_returns_entries_after(self) -> None:
        log = OperationLog()
        id1 = log.append(_make_create("A"))
        op2 = CreateNode(
            node=Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="P1"),
            parent_ops=(id1,),
            timestamp=utc_now(),
        )
        id2 = log.append(op2)
        since = log.entries_since(id1)
        assert len(since) == 1
        assert since[0].operation_id == id2


class TestAncestors:
    def test_chain_ancestors(self) -> None:
        log = OperationLog()
        id1 = log.append(_make_create("A"))
        op2 = CreateNode(
            node=Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="P"),
            parent_ops=(id1,),
            timestamp=utc_now(),
        )
        id2 = log.append(op2)
        ancestors = log.ancestors(id2)
        ancestor_ids = {a.operation_id for a in ancestors}
        assert id1 in ancestor_ids
        assert id2 in ancestor_ids


class TestIteration:
    def test_iter_returns_entries_in_order(self) -> None:
        log = OperationLog()
        id1 = log.append(_make_create("A"))
        op2 = CreateNode(
            node=Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="P"),
            parent_ops=(id1,),
            timestamp=utc_now(),
        )
        id2 = log.append(op2)
        entries = list(log)
        assert len(entries) == 2
        assert entries[0].operation_id == id1
        assert entries[1].operation_id == id2
        assert all(isinstance(e, LogEntry) for e in entries)

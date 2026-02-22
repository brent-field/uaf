"""Tests for audit log — AuditEntry, AuditAction, AuditOutcome, AuditLog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from uaf.core.node_id import NodeId, OperationId, utc_now
from uaf.security.audit import AuditAction, AuditEntry, AuditLog, AuditOutcome
from uaf.security.primitives import PrincipalId


def _pid(name: str) -> PrincipalId:
    return PrincipalId(value=name)


def _make_entry(
    *,
    principal: str = "alice",
    action: AuditAction = AuditAction.CREATE_NODE,
    outcome: AuditOutcome = AuditOutcome.ALLOWED,
    target_id: NodeId | None = None,
    artifact_id: NodeId | None = None,
    timestamp: datetime | None = None,
) -> AuditEntry:
    return AuditEntry(
        operation_id=OperationId(hex_digest="a" * 64),
        principal_id=_pid(principal),
        timestamp=timestamp or utc_now(),
        action=action,
        target_id=target_id or NodeId.generate(),
        artifact_id=artifact_id,
        outcome=outcome,
    )


class TestAuditEntryFrozen:
    def test_frozen(self) -> None:
        e = _make_entry()
        try:
            e.action = AuditAction.DELETE_NODE  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass


class TestAuditLogRecord:
    def test_count_starts_at_zero(self) -> None:
        log = AuditLog()
        assert log.count() == 0

    def test_record_increments_count(self) -> None:
        log = AuditLog()
        log.record(_make_entry())
        log.record(_make_entry())
        assert log.count() == 2


class TestAuditLogByPrincipal:
    def test_query_by_principal(self) -> None:
        log = AuditLog()
        log.record(_make_entry(principal="alice"))
        log.record(_make_entry(principal="bob"))
        log.record(_make_entry(principal="alice"))
        assert len(log.for_principal(_pid("alice"))) == 2
        assert len(log.for_principal(_pid("bob"))) == 1

    def test_query_unknown_principal(self) -> None:
        log = AuditLog()
        assert log.for_principal(_pid("nobody")) == []


class TestAuditLogByNode:
    def test_query_by_node(self) -> None:
        log = AuditLog()
        n1 = NodeId.generate()
        n2 = NodeId.generate()
        log.record(_make_entry(target_id=n1))
        log.record(_make_entry(target_id=n2))
        log.record(_make_entry(target_id=n1))
        assert len(log.for_node(n1)) == 2
        assert len(log.for_node(n2)) == 1


class TestAuditLogByArtifact:
    def test_query_by_artifact(self) -> None:
        log = AuditLog()
        a1 = NodeId.generate()
        a2 = NodeId.generate()
        log.record(_make_entry(artifact_id=a1))
        log.record(_make_entry(artifact_id=a2))
        log.record(_make_entry(artifact_id=a1))
        assert len(log.for_artifact(a1)) == 2

    def test_none_artifact_not_indexed(self) -> None:
        log = AuditLog()
        log.record(_make_entry(artifact_id=None))
        assert log.count() == 1
        # No artifact to query
        assert log.for_artifact(NodeId.generate()) == []


class TestAuditLogDenied:
    def test_denied_filter(self) -> None:
        log = AuditLog()
        log.record(_make_entry(outcome=AuditOutcome.ALLOWED))
        log.record(_make_entry(outcome=AuditOutcome.DENIED))
        log.record(_make_entry(outcome=AuditOutcome.DENIED))
        assert len(log.denied()) == 2

    def test_denied_empty_when_all_allowed(self) -> None:
        log = AuditLog()
        log.record(_make_entry(outcome=AuditOutcome.ALLOWED))
        assert log.denied() == []


class TestAuditLogTimeRange:
    def test_since_filter_on_principal(self) -> None:
        log = AuditLog()
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 6, 1, tzinfo=UTC)
        t3 = datetime(2025, 12, 1, tzinfo=UTC)
        log.record(_make_entry(principal="alice", timestamp=t1))
        log.record(_make_entry(principal="alice", timestamp=t2))
        log.record(_make_entry(principal="alice", timestamp=t3))
        cutoff = datetime(2025, 5, 1, tzinfo=UTC)
        result = log.for_principal(_pid("alice"), since=cutoff)
        assert len(result) == 2

    def test_since_filter_on_denied(self) -> None:
        log = AuditLog()
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 12, 1, tzinfo=UTC)
        log.record(_make_entry(outcome=AuditOutcome.DENIED, timestamp=t1))
        log.record(_make_entry(outcome=AuditOutcome.DENIED, timestamp=t2))
        cutoff = t2 - timedelta(days=1)
        assert len(log.denied(since=cutoff)) == 1

    def test_since_filter_on_node(self) -> None:
        log = AuditLog()
        node = NodeId.generate()
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 12, 1, tzinfo=UTC)
        log.record(_make_entry(target_id=node, timestamp=t1))
        log.record(_make_entry(target_id=node, timestamp=t2))
        cutoff = datetime(2025, 6, 1, tzinfo=UTC)
        assert len(log.for_node(node, since=cutoff)) == 1

    def test_since_filter_on_artifact(self) -> None:
        log = AuditLog()
        art = NodeId.generate()
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 12, 1, tzinfo=UTC)
        log.record(_make_entry(artifact_id=art, timestamp=t1))
        log.record(_make_entry(artifact_id=art, timestamp=t2))
        cutoff = datetime(2025, 6, 1, tzinfo=UTC)
        assert len(log.for_artifact(art, since=cutoff)) == 1

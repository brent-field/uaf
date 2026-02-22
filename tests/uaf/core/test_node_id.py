"""Tests for core identifiers — NodeId, EdgeId, OperationId, BlobId, utc_now."""

from datetime import UTC, datetime

import pytest

from uaf.core.node_id import BlobId, EdgeId, NodeId, OperationId, utc_now

# --- Valid hex digest for testing ---
VALID_HEX = "a" * 64


class TestNodeId:
    def test_generate_returns_node_id(self) -> None:
        nid = NodeId.generate()
        assert isinstance(nid, NodeId)

    def test_two_generated_ids_are_unique(self) -> None:
        a = NodeId.generate()
        b = NodeId.generate()
        assert a != b

    def test_is_hashable(self) -> None:
        nid = NodeId.generate()
        s = {nid}
        assert nid in s

    def test_is_frozen(self) -> None:
        nid = NodeId.generate()
        with pytest.raises(AttributeError):
            nid.value = nid.value  # type: ignore[misc]

    def test_str(self) -> None:
        nid = NodeId.generate()
        assert str(nid) == str(nid.value)


class TestEdgeId:
    def test_generate_returns_edge_id(self) -> None:
        eid = EdgeId.generate()
        assert isinstance(eid, EdgeId)

    def test_two_generated_ids_are_unique(self) -> None:
        a = EdgeId.generate()
        b = EdgeId.generate()
        assert a != b

    def test_is_hashable(self) -> None:
        eid = EdgeId.generate()
        s = {eid}
        assert eid in s


class TestOperationId:
    def test_valid_hex(self) -> None:
        oid = OperationId(hex_digest=VALID_HEX)
        assert oid.hex_digest == VALID_HEX

    def test_invalid_length_raises(self) -> None:
        with pytest.raises(ValueError, match="64-char"):
            OperationId(hex_digest="abc")

    def test_invalid_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="64-char"):
            OperationId(hex_digest="G" * 64)

    def test_uppercase_hex_raises(self) -> None:
        with pytest.raises(ValueError, match="64-char"):
            OperationId(hex_digest="A" * 64)

    def test_is_hashable(self) -> None:
        oid = OperationId(hex_digest=VALID_HEX)
        s = {oid}
        assert oid in s

    def test_is_frozen(self) -> None:
        oid = OperationId(hex_digest=VALID_HEX)
        with pytest.raises(AttributeError):
            oid.hex_digest = VALID_HEX  # type: ignore[misc]

    def test_str(self) -> None:
        oid = OperationId(hex_digest=VALID_HEX)
        assert str(oid) == VALID_HEX


class TestBlobId:
    def test_valid_hex(self) -> None:
        bid = BlobId(hex_digest=VALID_HEX)
        assert bid.hex_digest == VALID_HEX

    def test_invalid_length_raises(self) -> None:
        with pytest.raises(ValueError, match="64-char"):
            BlobId(hex_digest="abc")

    def test_is_hashable(self) -> None:
        bid = BlobId(hex_digest=VALID_HEX)
        s = {bid}
        assert bid in s

    def test_is_frozen(self) -> None:
        bid = BlobId(hex_digest=VALID_HEX)
        with pytest.raises(AttributeError):
            bid.hex_digest = VALID_HEX  # type: ignore[misc]


class TestUtcNow:
    def test_returns_datetime(self) -> None:
        now = utc_now()
        assert isinstance(now, datetime)

    def test_is_timezone_aware(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None

    def test_is_utc(self) -> None:
        now = utc_now()
        assert now.tzinfo == UTC

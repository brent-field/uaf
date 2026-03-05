"""Tests for the persistence store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uaf.core.node_id import utc_now
from uaf.core.nodes import Artifact, NodeType, make_node_metadata
from uaf.core.operations import CreateNode
from uaf.core.serialization import blob_hash
from uaf.db.store import Store, StoreConfig

if TYPE_CHECKING:
    from pathlib import Path


def _make_create_op() -> CreateNode:
    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Test")
    return CreateNode(node=art, parent_ops=(), timestamp=utc_now())


class TestStoreOpenOrCreate:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        store = Store.open_or_create(StoreConfig(root=root))

        assert root.exists()
        assert (root / "blobs").is_dir()
        assert (root / "META.json").exists()
        assert store.root == root

    def test_reopens_without_error(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)
        Store.open_or_create(config)
        store2 = Store.open_or_create(config)
        assert store2.root == root

    def test_metadata_on_creation(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        store = Store.open_or_create(StoreConfig(root=root))
        meta = store.read_metadata()
        assert meta["store_version"] == 1
        assert meta["operation_count"] == 0


class TestStoreBlobStorage:
    def test_store_and_get_blob(self, tmp_path: Path) -> None:
        store = Store.open_or_create(StoreConfig(root=tmp_path / "store"))

        data = b"hello world binary data"
        bid = store.store_blob(data)

        result = store.get_blob(bid)
        assert result == data

    def test_blob_content_addressed(self, tmp_path: Path) -> None:
        store = Store.open_or_create(StoreConfig(root=tmp_path / "store"))

        data = b"some bytes"
        bid = store.store_blob(data)
        expected_bid = blob_hash(data)
        assert bid == expected_bid

    def test_blob_deduplication(self, tmp_path: Path) -> None:
        store = Store.open_or_create(StoreConfig(root=tmp_path / "store"))

        data = b"duplicate content"
        bid1 = store.store_blob(data)
        bid2 = store.store_blob(data)

        assert bid1 == bid2
        # Verify only one file exists
        blob_dir = tmp_path / "store" / "blobs"
        blob_files = list(blob_dir.iterdir())
        assert len(blob_files) == 1

    def test_get_missing_blob(self, tmp_path: Path) -> None:
        store = Store.open_or_create(StoreConfig(root=tmp_path / "store"))
        fake_bid = blob_hash(b"nonexistent")
        assert store.get_blob(fake_bid) is None

    def test_blob_survives_new_store_instance(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)
        store1 = Store.open_or_create(config)
        data = b"persistent blob"
        bid = store1.store_blob(data)

        store2 = Store.open_or_create(config)
        assert store2.get_blob(bid) == data


class TestStoreMetadata:
    def test_write_and_read_metadata(self, tmp_path: Path) -> None:
        store = Store.open_or_create(StoreConfig(root=tmp_path / "store"))
        store.write_metadata(42)

        meta = store.read_metadata()
        assert meta["operation_count"] == 42
        assert meta["store_version"] == 1

    def test_read_metadata_missing(self, tmp_path: Path) -> None:
        store = Store(StoreConfig(root=tmp_path / "no-store"))
        assert store.read_metadata() == {}


class TestStoreJournalIntegration:
    def test_journal_accessible(self, tmp_path: Path) -> None:
        store = Store.open_or_create(StoreConfig(root=tmp_path / "store"))
        op = _make_create_op()
        store.journal.append(op)
        store.journal.close()

        assert store.journal.count() == 1

    def test_journal_path_in_store_root(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        store = Store.open_or_create(StoreConfig(root=root))
        assert store.journal.path == root / "operations.jsonl"


class TestStoreDeleteAll:
    def test_delete_all_removes_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        store = Store.open_or_create(StoreConfig(root=root))

        store.store_blob(b"data")
        store.journal.append(_make_create_op())
        store.journal.close()

        store.delete_all()
        assert not root.exists()

    def test_delete_store_static(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        Store.open_or_create(StoreConfig(root=root))
        assert root.exists()

        Store.delete_store(root)
        assert not root.exists()

    def test_delete_store_nonexistent_is_safe(self, tmp_path: Path) -> None:
        Store.delete_store(tmp_path / "nope")  # Should not raise

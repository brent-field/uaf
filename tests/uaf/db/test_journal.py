"""Tests for the JSONL persistence layer — JournaledGraphDB, JournalWriter/Reader."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import BlobId, EdgeId, NodeId, utc_now
from uaf.core.nodes import Artifact, Heading, NodeType, Paragraph, make_node_metadata

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _art(title: str = "Test") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _para(text: str = "Hello world") -> Paragraph:
    return Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)


def _heading(text: str = "H1", level: int = 1) -> Heading:
    return Heading(meta=make_node_metadata(NodeType.HEADING), text=text, level=level)


def _contains(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# TestJournalWriter
# ---------------------------------------------------------------------------


class TestJournalWriter:
    """Verify the JSONL journal file is written correctly."""

    def test_append_creates_jsonl_file(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        db.create_node(_art("A"))
        assert (tmp_path / "store" / "journal.jsonl").exists()

    def test_append_writes_one_line_per_op(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        db.create_node(_art("A"))
        db.create_node(_para("P1"))
        db.create_node(_para("P2"))

        lines = (tmp_path / "store" / "journal.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        db.create_node(_art("A"))
        db.create_node(_para("P1"))

        for line in (tmp_path / "store" / "journal.jsonl").read_text().strip().splitlines():
            parsed = json.loads(line)
            assert isinstance(parsed, dict)
            assert "__type__" in parsed

    def test_line_matches_operation_to_dict(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        art = _art("Check")
        db.create_node(art)

        line = (tmp_path / "store" / "journal.jsonl").read_text().strip().splitlines()[0]
        parsed = json.loads(line)
        assert parsed["__type__"] == "CreateNode"
        assert parsed["node"]["title"] == "Check"

    def test_canonical_json_determinism(self, tmp_path: Path) -> None:
        """Same operation written twice in different DBs produces identical JSONL lines."""
        from uaf.core.operations import CreateNode
        from uaf.db.journal import JournaledGraphDB

        art = _art("Deterministic")
        # Build a single operation with a fixed timestamp so both writes are identical
        op = CreateNode(node=art, parent_ops=(), timestamp=utc_now())

        db1 = JournaledGraphDB(tmp_path / "store1")
        db1.apply(op)
        line1 = (tmp_path / "store1" / "journal.jsonl").read_text().strip()

        db2 = JournaledGraphDB(tmp_path / "store2")
        db2.apply(op)
        line2 = (tmp_path / "store2" / "journal.jsonl").read_text().strip()

        assert line1 == line2


# ---------------------------------------------------------------------------
# TestJournalReplay
# ---------------------------------------------------------------------------


class TestJournalReplay:
    """Verify that state is correctly restored from a journal on restart."""

    def test_empty_journal_starts_fresh(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        assert db.count_nodes() == 0

    def test_replay_restores_nodes(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        art = _art("Restored")
        art_id = db1.create_node(art)

        # New instance from same store
        db2 = JournaledGraphDB(store)
        restored = db2.get_node(art_id)
        assert restored is not None
        assert isinstance(restored, Artifact)
        assert restored.title == "Restored"

    def test_replay_restores_edges(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        art = _art("Parent")
        para = _para("Child")
        db1.create_node(art)
        db1.create_node(para)
        db1.create_edge(_contains(art.meta.id, para.meta.id))

        db2 = JournaledGraphDB(store)
        children = db2.get_children(art.meta.id)
        assert len(children) == 1
        assert children[0].meta.id == para.meta.id

    def test_replay_restores_blobs(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        data = b"Hello blob"
        bid = db1.store_blob(data)

        db2 = JournaledGraphDB(store)
        assert db2.get_blob(bid) == data

    def test_replay_preserves_children_order(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        art = _art("Ordered")
        p1 = _para("First")
        p2 = _para("Second")
        p3 = _para("Third")
        db1.create_node(art)
        for p in [p1, p2, p3]:
            db1.create_node(p)
            db1.create_edge(_contains(art.meta.id, p.meta.id))

        db2 = JournaledGraphDB(store)
        children = db2.get_children(art.meta.id)
        assert [c.text for c in children] == ["First", "Second", "Third"]

    def test_replay_handles_update_and_delete(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        art = _art("Original")
        art_id = db1.create_node(art)
        # Update
        updated = Artifact(meta=art.meta, title="Updated")
        db1.update_node(updated)
        # Create and delete another node
        para = _para("Temp")
        para_id = db1.create_node(para)
        db1.delete_node(para_id)

        db2 = JournaledGraphDB(store)
        restored = db2.get_node(art_id)
        assert isinstance(restored, Artifact)
        assert restored.title == "Updated"
        assert db2.get_node(para_id) is None

    def test_partial_last_line_skipped(self, tmp_path: Path) -> None:
        """Simulates a crash mid-write: truncated last line is ignored."""
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        art = _art("Safe")
        db1.create_node(art)

        # Corrupt the journal by appending a partial line
        journal_path = store / "journal.jsonl"
        with open(journal_path, "a") as f:
            f.write('{"__type__": "CreateNode", "node": {')  # incomplete

        # Should still replay the valid first line
        db2 = JournaledGraphDB(store)
        assert db2.count_nodes() == 1


# ---------------------------------------------------------------------------
# TestFileBlobStore
# ---------------------------------------------------------------------------


class TestFileBlobStore:
    """Verify content-addressed file-based blob storage."""

    def test_store_blob_writes_file(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        data = b"binary content"
        db.store_blob(data)
        # Verify file exists under blobs directory
        blob_dir = tmp_path / "store" / "blobs"
        assert blob_dir.exists()
        # Find the file by walking
        blob_files = list(blob_dir.rglob("*"))
        blob_files = [f for f in blob_files if f.is_file()]
        assert len(blob_files) == 1

    def test_get_blob_reads_from_disk(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        data = b"persistent blob"
        bid = db1.store_blob(data)

        db2 = JournaledGraphDB(store)
        assert db2.get_blob(bid) == data

    def test_deduplication(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        data = b"same data"
        bid1 = db.store_blob(data)
        bid2 = db.store_blob(data)
        assert bid1 == bid2
        # Only one file on disk
        blob_files = list((tmp_path / "store" / "blobs").rglob("*"))
        blob_files = [f for f in blob_files if f.is_file()]
        assert len(blob_files) == 1

    def test_missing_blob_returns_none(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        fake_bid = BlobId(hex_digest="0" * 64)
        assert db.get_blob(fake_bid) is None


# ---------------------------------------------------------------------------
# TestJournaledGraphDBInterface
# ---------------------------------------------------------------------------


class TestJournaledGraphDBInterface:
    """Verify JournaledGraphDB is a drop-in replacement for GraphDB."""

    def test_all_graphdb_methods_work(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        art = _art("Methods Test")
        art_id = db.create_node(art)
        assert db.get_node(art_id) is not None

        para = _para("P")
        para_id = db.create_node(para)
        edge = _contains(art_id, para_id)
        db.create_edge(edge)

        assert len(db.get_children(art_id)) == 1
        assert db.count_nodes() == 2
        assert db.count_edges() == 1

        db.delete_edge(edge.id)
        assert db.count_edges() == 0

        db.delete_node(para_id)
        assert db.count_nodes() == 1

    def test_is_drop_in_replacement(self, tmp_path: Path) -> None:
        """JournaledGraphDB IS-A GraphDB."""
        from uaf.db.graph_db import GraphDB
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        assert isinstance(db, GraphDB)

    def test_count_nodes_after_replay(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        db1.create_node(_art("A"))
        db1.create_node(_para("P"))
        assert db1.count_nodes() == 2

        db2 = JournaledGraphDB(store)
        assert db2.count_nodes() == 2


# ---------------------------------------------------------------------------
# TestDevDataManagement
# ---------------------------------------------------------------------------


class TestDevDataManagement:
    """Verify dev-only data management tools."""

    def test_reset_wipes_all_data(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db = JournaledGraphDB(store)
        db.create_node(_art("A"))
        db.store_blob(b"data")
        assert db.count_nodes() == 1

        db.reset()
        assert db.count_nodes() == 0
        # Journal file should be gone or empty
        journal = store / "journal.jsonl"
        assert not journal.exists() or journal.read_text() == ""

    def test_delete_artifact_removes_subtree(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")
        art = _art("Parent")
        p1 = _para("Child 1")
        p2 = _para("Child 2")
        art_id = db.create_node(art)
        db.create_node(p1)
        db.create_node(p2)
        db.create_edge(_contains(art_id, p1.meta.id))
        db.create_edge(_contains(art_id, p2.meta.id))
        assert db.count_nodes() == 3

        db.delete_artifact(art_id)
        assert db.count_nodes() == 0

    def test_deletion_survives_restart(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        art = _art("Gone")
        art_id = db1.create_node(art)
        p = _para("Also gone")
        db1.create_node(p)
        db1.create_edge(_contains(art_id, p.meta.id))
        db1.delete_artifact(art_id)

        db2 = JournaledGraphDB(store)
        assert db2.count_nodes() == 0
        assert db2.get_node(art_id) is None

    def test_reset_flag_clears_store(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        db1.create_node(_art("Exists"))
        assert db1.count_nodes() == 1

        # Simulate --reset by passing reset=True
        db2 = JournaledGraphDB(store, reset=True)
        assert db2.count_nodes() == 0


# ---------------------------------------------------------------------------
# TestStoreDirectory
# ---------------------------------------------------------------------------


class TestStoreDirectory:
    """Verify store directory management."""

    def test_store_dir_created_on_init(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "new_store"
        assert not store.exists()
        JournaledGraphDB(store)
        assert store.exists()
        assert (store / "blobs").exists()

    def test_custom_store_path(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        custom = tmp_path / "deeply" / "nested" / "store"
        db = JournaledGraphDB(custom)
        db.create_node(_art("Custom Path"))
        assert (custom / "journal.jsonl").exists()

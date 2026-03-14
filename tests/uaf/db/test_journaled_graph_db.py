"""Tests for JournaledGraphDB — persistence wrapper around GraphDB."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Heading,
    NodeType,
    Paragraph,
    make_node_metadata,
)
from uaf.core.operations import CreateNode
from uaf.db.journaled_graph_db import JournaledGraphDB
from uaf.db.store import Store, StoreConfig

if TYPE_CHECKING:
    from pathlib import Path


def _make_store(tmp_path: Path) -> Store:
    return Store.open_or_create(StoreConfig(root=tmp_path / "store"))


def _make_artifact(title: str = "Test") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _make_paragraph(text: str = "Hello") -> Paragraph:
    return Paragraph(
        meta=make_node_metadata(NodeType.PARAGRAPH), text=text, style="body"
    )


def _make_heading(text: str = "Title", level: int = 1) -> Heading:
    return Heading(
        meta=make_node_metadata(NodeType.HEADING), text=text, level=level
    )


def _make_contains_edge(parent: NodeId, child: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=parent,
        target=child,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


class TestJournaledGraphDBBasic:
    def test_apply_persists_to_journal(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        db = JournaledGraphDB(store)

        art = _make_artifact("Doc")
        db.create_node(art)

        store.journal.close()
        assert store.journal.count() == 1

    def test_create_and_query_node(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        db = JournaledGraphDB(store)

        art = _make_artifact("MyDoc")
        nid = db.create_node(art)

        node = db.get_node(nid)
        assert node is not None
        assert node.title == "MyDoc"

    def test_count_nodes(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        db = JournaledGraphDB(store)

        db.create_node(_make_artifact("A"))
        db.create_node(_make_paragraph("text"))
        assert db.count_nodes() == 2


class TestJournaledGraphDBReplay:
    def test_replay_on_construction(self, tmp_path: Path) -> None:
        """State rebuilt from journal when constructing a new instance."""
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)
        art = _make_artifact("Persistent")
        nid = db1.create_node(art)
        store1.journal.close()

        # "Restart": new store + db instance
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        node = db2.get_node(nid)
        assert node is not None
        assert node.title == "Persistent"

    def test_full_workflow_survives_restart(self, tmp_path: Path) -> None:
        """Artifact with children and edges survives restart."""
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        art = _make_artifact("Report")
        h1 = _make_heading("Chapter 1", level=1)
        p1 = _make_paragraph("Some text here")

        art_id = db1.create_node(art)
        h1_id = db1.create_node(h1)
        p1_id = db1.create_node(p1)

        db1.create_edge(_make_contains_edge(art_id, h1_id))
        db1.create_edge(_make_contains_edge(art_id, p1_id))
        store1.journal.close()

        # Restart
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        assert db2.count_nodes() == 3
        assert db2.count_edges() == 2

        children = db2.get_children(art_id)
        assert len(children) == 2

        node = db2.get_node(h1_id)
        assert node is not None
        assert node.text == "Chapter 1"

    def test_replay_rebuilds_eavt_index(self, tmp_path: Path) -> None:
        """EAVT indexes work after replay (find_by_type, find_by_attribute)."""
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        db1.create_node(_make_artifact("Doc1"))
        db1.create_node(_make_artifact("Doc2"))
        db1.create_node(_make_paragraph("Hello"))
        store1.journal.close()

        # Restart
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        artifacts = db2.find_by_type(NodeType.ARTIFACT)
        assert len(artifacts) == 2

        paragraphs = db2.find_by_type(NodeType.PARAGRAPH)
        assert len(paragraphs) == 1

    def test_history_survives_restart(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        art = _make_artifact("Versioned")
        art_id = db1.create_node(art)
        store1.journal.close()

        # Restart
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        history = db2.get_history(art_id)
        assert len(history) == 1

    def test_update_and_delete_survive_restart(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        art = _make_artifact("Original")
        art_id = db1.create_node(art)

        updated = Artifact(meta=art.meta, title="Updated")
        db1.update_node(updated)

        p = _make_paragraph("temp")
        p_id = db1.create_node(p)
        db1.delete_node(p_id)
        store1.journal.close()

        # Restart
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        node = db2.get_node(art_id)
        assert node is not None
        assert node.title == "Updated"

        assert db2.get_node(p_id) is None


class TestJournaledGraphDBBlobs:
    def test_blob_survives_restart(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        data = b"binary image data here"
        bid = db1.store_blob(data)

        # Verify in-memory
        assert db1.get_blob(bid) == data
        store1.journal.close()

        # Restart (new instance, empty in-memory cache)
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        # Should read from disk
        assert db2.get_blob(bid) == data

    def test_get_missing_blob(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        db = JournaledGraphDB(store)

        from uaf.core.serialization import blob_hash

        fake_bid = blob_hash(b"nonexistent")
        assert db.get_blob(fake_bid) is None


class TestJournaledGraphDBDeleteStore:
    def test_delete_store_wipes_everything(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)
        db1.create_node(_make_artifact("Gone"))
        store1.journal.close()

        store1.delete_all()

        # Re-create from scratch
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        assert db2.count_nodes() == 0


class TestJournaledGraphDBMetadata:
    def test_metadata_updated_on_apply(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        db = JournaledGraphDB(store)

        db.create_node(_make_artifact("A"))
        db.create_node(_make_artifact("B"))

        meta = store.read_metadata()
        assert meta["operation_count"] == 2


class TestJournaledGraphDBUndoPersistence:
    """Tests for undo/redo stack persistence across restarts."""

    def test_undo_survives_restart(self, tmp_path: Path) -> None:
        """Create nodes, restart, undo still works."""
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        art = _make_artifact("Doc")
        art_id = db1.create_node(art)
        p = _make_paragraph("text")
        with db1.action_group("u", "art1"):
            op = CreateNode(
                node=p, parent_ops=(), timestamp=utc_now(), principal_id="u",
            )
            db1.apply(op)
        store1.journal.close()
        store1.undo_journal.close()

        # Restart
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        assert db2.get_node(p.meta.id) is not None

        # Undo should remove the paragraph
        db2.undo("u", "art1")
        assert db2.get_node(p.meta.id) is None
        # Artifact should still be there
        assert db2.get_node(art_id) is not None

    def test_redo_survives_restart(self, tmp_path: Path) -> None:
        """Create, undo, restart, redo still works."""
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        p = _make_paragraph("redoable")
        with db1.action_group("u", "art1"):
            op = CreateNode(
                node=p, parent_ops=(), timestamp=utc_now(), principal_id="u",
            )
            db1.apply(op)
        db1.undo("u", "art1")
        assert db1.get_node(p.meta.id) is None
        store1.journal.close()
        store1.undo_journal.close()

        # Restart
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        assert db2.get_node(p.meta.id) is None
        # Redo should restore the paragraph
        db2.redo("u", "art1")
        assert db2.get_node(p.meta.id) is not None

    def test_compensating_ops_journaled(self, tmp_path: Path) -> None:
        """Undo, restart — graph state reflects the undo."""
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)

        p = _make_paragraph("will-undo")
        with db1.action_group("u", "art1"):
            op = CreateNode(
                node=p, parent_ops=(), timestamp=utc_now(), principal_id="u",
            )
            db1.apply(op)
        db1.undo("u", "art1")
        # Node should be gone
        assert db1.get_node(p.meta.id) is None
        store1.journal.close()
        store1.undo_journal.close()

        # Restart — compensating ops were journaled, so state is correct
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        assert db2.get_node(p.meta.id) is None

    def test_no_undo_journal_backward_compat(self, tmp_path: Path) -> None:
        """Old store without undo_groups.jsonl starts fine."""
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        # Create store and write an operation, but no undo journal file
        store1 = Store.open_or_create(config)
        db1 = JournaledGraphDB(store1)
        p = _make_paragraph("old-store")
        db1.create_node(p)
        store1.journal.close()
        store1.undo_journal.close()

        # Delete the undo journal file to simulate an old store
        undo_path = root / "undo_groups.jsonl"
        if undo_path.exists():
            undo_path.unlink()

        # Restart should work — no undo history, but no crash
        store2 = Store.open_or_create(config)
        db2 = JournaledGraphDB(store2)

        assert db2.get_node(p.meta.id) is not None
        # Undo returns empty since no undo journal was found
        result = db2.undo("u", "art1")
        assert result == []

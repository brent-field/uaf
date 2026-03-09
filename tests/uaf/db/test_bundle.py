"""Tests for the UAF bundle export/import (.uaf zip format)."""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

import pytest

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

if TYPE_CHECKING:
    from pathlib import Path


def _make_artifact(db: GraphDB, title: str = "Test Doc") -> NodeId:
    """Helper: create an artifact with a heading and a paragraph."""
    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)
    art_id = db.create_node(art)

    h = Heading(meta=make_node_metadata(NodeType.HEADING), text="Introduction", level=1)
    h_id = db.create_node(h)
    db.create_edge(Edge(
        id=EdgeId.generate(), source=art_id, target=h_id,
        edge_type=EdgeType.CONTAINS, created_at=utc_now(),
    ))

    p = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Hello, world!")
    p_id = db.create_node(p)
    db.create_edge(Edge(
        id=EdgeId.generate(), source=art_id, target=p_id,
        edge_type=EdgeType.CONTAINS, created_at=utc_now(),
    ))

    return art_id


# ---------------------------------------------------------------------------
# TestBundleExport
# ---------------------------------------------------------------------------


class TestBundleExport:
    """Verify .uaf bundle export produces a valid zip."""

    def test_export_creates_valid_zip(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art_id = _make_artifact(db)
        out = tmp_path / "test.uaf"
        export_bundle(db, [art_id], out)

        assert out.exists()
        assert zipfile.is_zipfile(out)

    def test_export_contains_manifest(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art_id = _make_artifact(db)
        out = tmp_path / "test.uaf"
        export_bundle(db, [art_id], out)

        with zipfile.ZipFile(out) as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
            assert "version" in manifest
            assert "artifacts" in manifest
            assert len(manifest["artifacts"]) == 1

    def test_export_contains_journal(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art_id = _make_artifact(db)
        out = tmp_path / "test.uaf"
        export_bundle(db, [art_id], out)

        with zipfile.ZipFile(out) as zf:
            assert "journal.jsonl" in zf.namelist()
            lines = zf.read("journal.jsonl").decode("utf-8").strip().splitlines()
            # At least: create artifact + create heading + create edge + create para + create edge
            assert len(lines) >= 5

    def test_export_manifest_has_artifact_metadata(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art_id = _make_artifact(db, title="My Document")
        out = tmp_path / "test.uaf"
        export_bundle(db, [art_id], out)

        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            art_info = manifest["artifacts"][0]
            assert art_info["id"] == str(art_id)
            assert art_info["title"] == "My Document"

    def test_export_subtree_only(self, tmp_path: Path) -> None:
        """Exporting one artifact should not include ops from another."""
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art1_id = _make_artifact(db, title="Doc One")
        _make_artifact(db, title="Doc Two")  # second artifact not exported

        out = tmp_path / "test.uaf"
        export_bundle(db, [art1_id], out)

        with zipfile.ZipFile(out) as zf:
            journal_text = zf.read("journal.jsonl").decode("utf-8")
            # Shouldn't contain "Doc Two" references
            assert "Doc Two" not in journal_text

    def test_export_includes_blobs(self, tmp_path: Path) -> None:
        """If an artifact references blobs, the bundle should include them."""
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Blob Doc")
        art_id = db.create_node(art)

        # Store a blob
        blob_data = b"binary-image-data"
        bid = db.store_blob(blob_data)

        # Create an Image node referencing the blob
        from uaf.core.nodes import Image

        img = Image(
            meta=make_node_metadata(NodeType.IMAGE),
            uri=f"blob:{bid.hex_digest}",
        )
        img_id = db.create_node(img)
        db.create_edge(Edge(
            id=EdgeId.generate(), source=art_id, target=img_id,
            edge_type=EdgeType.CONTAINS, created_at=utc_now(),
        ))

        out = tmp_path / "test.uaf"
        export_bundle(db, [art_id], out)

        with zipfile.ZipFile(out) as zf:
            blob_path = f"blobs/{bid.hex_digest}"
            assert blob_path in zf.namelist()
            assert zf.read(blob_path) == blob_data


# ---------------------------------------------------------------------------
# TestBundleImport
# ---------------------------------------------------------------------------


class TestBundleImport:
    """Verify .uaf bundle import restores data correctly."""

    def test_import_creates_artifact(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle, import_bundle

        db1 = GraphDB()
        art_id = _make_artifact(db1)
        bundle_path = tmp_path / "test.uaf"
        export_bundle(db1, [art_id], bundle_path)

        db2 = GraphDB()
        imported_ids = import_bundle(db2, bundle_path)
        assert len(imported_ids) == 1
        art = db2.get_node(imported_ids[0])
        assert isinstance(art, Artifact)
        assert art.title == "Test Doc"

    def test_import_restores_nodes(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle, import_bundle

        db1 = GraphDB()
        art_id = _make_artifact(db1)
        bundle_path = tmp_path / "test.uaf"
        export_bundle(db1, [art_id], bundle_path)

        db2 = GraphDB()
        imported_ids = import_bundle(db2, bundle_path)
        children = db2.get_children(imported_ids[0])
        assert len(children) == 2  # heading + paragraph

    def test_import_restores_blobs(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle, import_bundle

        db1 = GraphDB()
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Blob Doc")
        art_id = db1.create_node(art)
        blob_data = b"test-blob-content"
        bid = db1.store_blob(blob_data)

        from uaf.core.nodes import Image

        img = Image(meta=make_node_metadata(NodeType.IMAGE), uri=f"blob:{bid.hex_digest}")
        img_id = db1.create_node(img)
        db1.create_edge(Edge(
            id=EdgeId.generate(), source=art_id, target=img_id,
            edge_type=EdgeType.CONTAINS, created_at=utc_now(),
        ))

        bundle_path = tmp_path / "test.uaf"
        export_bundle(db1, [art_id], bundle_path)

        db2 = GraphDB()
        import_bundle(db2, bundle_path)
        restored = db2.get_blob(bid)
        assert restored == blob_data

    def test_import_preserves_structure(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle, import_bundle

        db1 = GraphDB()
        art_id = _make_artifact(db1)
        bundle_path = tmp_path / "test.uaf"
        export_bundle(db1, [art_id], bundle_path)

        db2 = GraphDB()
        imported_ids = import_bundle(db2, bundle_path)
        children = db2.get_children(imported_ids[0])
        assert isinstance(children[0], Heading)
        assert children[0].text == "Introduction"
        assert isinstance(children[1], Paragraph)
        assert children[1].text == "Hello, world!"

    def test_import_no_conflict_with_existing_data(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle, import_bundle

        db1 = GraphDB()
        art_id = _make_artifact(db1)
        bundle_path = tmp_path / "test.uaf"
        export_bundle(db1, [art_id], bundle_path)

        # db2 already has some data
        db2 = GraphDB()
        existing_art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT), title="Existing",
        )
        db2.create_node(existing_art)

        imported_ids = import_bundle(db2, bundle_path)
        # Both original and imported should coexist
        assert db2.get_node(existing_art.meta.id) is not None
        assert db2.get_node(imported_ids[0]) is not None

    def test_import_invalid_bundle_raises(self, tmp_path: Path) -> None:
        from uaf.db.bundle import import_bundle

        bad_zip = tmp_path / "bad.uaf"
        bad_zip.write_bytes(b"not-a-zip")

        db = GraphDB()
        with pytest.raises(Exception):  # noqa: B017
            import_bundle(db, bad_zip)


# ---------------------------------------------------------------------------
# TestSnapshotMode
# ---------------------------------------------------------------------------


class TestSnapshotMode:
    """Verify snapshot export (current state only, no full history)."""

    def test_snapshot_produces_synthetic_ops(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art_id = _make_artifact(db)

        # Also do an update to add history
        art = db.get_node(art_id)
        from dataclasses import replace

        updated = replace(art, title="Updated Title")
        db.update_node(updated)

        full_path = tmp_path / "full.uaf"
        snap_path = tmp_path / "snap.uaf"
        export_bundle(db, [art_id], full_path, snapshot=False)
        export_bundle(db, [art_id], snap_path, snapshot=True)

        with zipfile.ZipFile(snap_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["mode"] == "snapshot"

    def test_snapshot_is_smaller(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art_id = _make_artifact(db)

        # Add update history
        art = db.get_node(art_id)
        from dataclasses import replace

        for i in range(5):
            updated = replace(art, title=f"Title v{i}")
            db.update_node(updated)
            art = db.get_node(art_id)

        full_path = tmp_path / "full.uaf"
        snap_path = tmp_path / "snap.uaf"
        export_bundle(db, [art_id], full_path, snapshot=False)
        export_bundle(db, [art_id], snap_path, snapshot=True)

        assert snap_path.stat().st_size <= full_path.stat().st_size

    def test_snapshot_import_matches_current_state(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle, import_bundle

        db1 = GraphDB()
        art_id = _make_artifact(db1)
        art = db1.get_node(art_id)
        from dataclasses import replace

        updated = replace(art, title="Final Title")
        db1.update_node(updated)

        snap_path = tmp_path / "snap.uaf"
        export_bundle(db1, [art_id], snap_path, snapshot=True)

        db2 = GraphDB()
        imported_ids = import_bundle(db2, snap_path)
        imported_art = db2.get_node(imported_ids[0])
        assert isinstance(imported_art, Artifact)
        assert imported_art.title == "Final Title"

        children = db2.get_children(imported_ids[0])
        assert len(children) == 2  # heading + paragraph preserved


# ---------------------------------------------------------------------------
# TestMultiArtifactExport
# ---------------------------------------------------------------------------


class TestMultiArtifactExport:
    """Verify exporting multiple artifacts in a single bundle."""

    def test_multi_artifact_bundle(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle, import_bundle

        db = GraphDB()
        art1_id = _make_artifact(db, title="Doc One")
        art2_id = _make_artifact(db, title="Doc Two")

        out = tmp_path / "multi.uaf"
        export_bundle(db, [art1_id, art2_id], out)

        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert len(manifest["artifacts"]) == 2

        # Import into fresh db
        db2 = GraphDB()
        imported = import_bundle(db2, out)
        assert len(imported) == 2

    def test_multi_artifact_titles(self, tmp_path: Path) -> None:
        from uaf.db.bundle import export_bundle

        db = GraphDB()
        art1_id = _make_artifact(db, title="Alpha")
        art2_id = _make_artifact(db, title="Beta")

        out = tmp_path / "multi.uaf"
        export_bundle(db, [art1_id, art2_id], out)

        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            titles = {a["title"] for a in manifest["artifacts"]}
            assert titles == {"Alpha", "Beta"}

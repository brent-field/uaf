"""JSONL persistence layer — JournaledGraphDB, JournalWriter, JournalReader, FileBlobStore."""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

from uaf.core.operations import operation_from_dict, operation_to_dict
from uaf.core.serialization import blob_hash, canonical_json
from uaf.db.graph_db import GraphDB

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.core.node_id import BlobId, NodeId, OperationId


class JournalWriter:
    """Appends one JSON line per operation to the journal file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file = open(path, "a")  # noqa: SIM115

    def append(self, op: Any) -> None:
        """Serialize an operation and append as a single JSONL line."""
        d = operation_to_dict(op)
        line = canonical_json(d).decode("utf-8")
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class JournalReader:
    """Reads JSONL journal, yields Operations. Skips malformed last line (crash tolerance)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def replay(self) -> list[Any]:
        """Read all valid operations from the journal."""
        if not self._path.exists():
            return []

        ops: list[Any] = []
        lines = self._path.read_text().splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                d = json.loads(stripped)
                op = operation_from_dict(d)
                ops.append(op)
            except (json.JSONDecodeError, KeyError, ValueError):
                # Tolerate a malformed last line (simulates crash mid-write)
                if i == len(lines) - 1:
                    continue
                raise
        return ops


class FileBlobStore:
    """Content-addressed file-based blob storage at {blob_dir}/{hash[0:2]}/{hash[2:4]}/{hash}."""

    def __init__(self, blob_dir: Path) -> None:
        self._dir = blob_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, blob_id: BlobId) -> Path:
        h = blob_id.hex_digest
        return self._dir / h[0:2] / h[2:4] / h

    def store(self, data: bytes) -> BlobId:
        """Write blob to disk. Deduplicates by content hash."""
        bid = blob_hash(data)
        path = self._blob_path(bid)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return bid

    def get(self, blob_id: BlobId) -> bytes | None:
        """Read blob from disk, or None if not found."""
        path = self._blob_path(blob_id)
        if path.exists():
            return path.read_bytes()
        return None


class JournaledGraphDB(GraphDB):
    """GraphDB with JSONL journal persistence and file-backed blob store.

    Extends GraphDB (IS-A, not wrapper). On init: creates directories, replays
    existing journal to rebuild in-memory state. All mutations are appended to
    the journal. Blobs are stored on disk.
    """

    def __init__(self, store_dir: Path, *, reset: bool = False) -> None:
        super().__init__()
        self._store_dir = store_dir

        if reset and store_dir.exists():
            shutil.rmtree(store_dir)

        store_dir.mkdir(parents=True, exist_ok=True)

        self._blob_store = FileBlobStore(store_dir / "blobs")
        self._journal_path = store_dir / "journal.jsonl"

        # Replay existing journal to rebuild in-memory state
        reader = JournalReader(self._journal_path)
        for op in reader.replay():
            super().apply(op)

        # Open writer for new operations (append mode)
        self._writer = JournalWriter(self._journal_path)

    def apply(self, op: Any) -> OperationId:
        """Apply operation to in-memory state and append to journal."""
        op_id = super().apply(op)
        self._writer.append(op)
        return op_id

    def store_blob(self, data: bytes) -> BlobId:
        """Store blob in memory and on disk."""
        bid = super().store_blob(data)
        self._blob_store.store(data)
        return bid

    def get_blob(self, blob_id: BlobId) -> bytes | None:
        """Get blob from memory, falling back to disk after restart."""
        result = super().get_blob(blob_id)
        if result is not None:
            return result
        # After replay, blobs are on disk but not in memory
        data = self._blob_store.get(blob_id)
        if data is not None:
            self._blobs[blob_id] = data
        return data

    def reset(self) -> None:
        """Dev-only: wipe all data (journal + blobs) and reinitialize empty."""
        self._writer.close()
        if self._store_dir.exists():
            shutil.rmtree(self._store_dir)
        self._store_dir.mkdir(parents=True, exist_ok=True)

        # Reinitialize in-memory state
        super().__init__()
        self._blob_store = FileBlobStore(self._store_dir / "blobs")
        self._writer = JournalWriter(self._journal_path)

    def delete_artifact(self, artifact_id: NodeId) -> None:
        """Dev-only: delete an artifact and its entire subtree (nodes + edges).

        Appends compensating DeleteEdge/DeleteNode ops to the journal so the
        deletion survives restart.
        """
        # Collect all descendant IDs before modifying anything
        all_ids = self.descendants(artifact_id)

        # Collect all edges originating from nodes in the subtree
        edges_to_delete = []
        for nid in all_ids:
            edges_to_delete.extend(self.get_edges_from(nid))

        # Delete edges first
        for edge in edges_to_delete:
            self.delete_edge(edge.id)

        # Delete child nodes, then the artifact itself
        for nid in all_ids:
            if nid != artifact_id:
                self.delete_node(nid)
        self.delete_node(artifact_id)

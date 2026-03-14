"""Persistence store — manages the on-disk directory for journal, blobs, and metadata."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from uaf.core.serialization import blob_hash
from uaf.db.journal import Journal, JournalConfig
from uaf.db.undo_journal import UndoJournal, UndoJournalConfig

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.core.node_id import BlobId

_STORE_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoreConfig:
    """Configuration for the persistence store."""

    root: Path
    flush_on_write: bool = True


class Store:
    """Manages the on-disk store directory: journal, blobs, and metadata.

    Layout::

        <root>/
            operations.jsonl   # Append-only operation journal
            blobs/             # Content-addressed binary files
                <sha256-hex>
            META.json          # Store version + operation count
    """

    def __init__(self, config: StoreConfig) -> None:
        self._config = config
        self._journal = Journal(JournalConfig(
            path=config.root / "operations.jsonl",
            flush_on_write=config.flush_on_write,
        ))
        self._undo_journal = UndoJournal(UndoJournalConfig(
            path=config.root / "undo_groups.jsonl",
            flush_on_write=config.flush_on_write,
        ))

    @classmethod
    def open_or_create(cls, config: StoreConfig) -> Store:
        """Open an existing store or create a new one."""
        config.root.mkdir(parents=True, exist_ok=True)
        (config.root / "blobs").mkdir(exist_ok=True)

        meta_path = config.root / "META.json"
        if not meta_path.exists():
            meta_path.write_text(json.dumps({
                "store_version": _STORE_VERSION,
                "operation_count": 0,
            }))

        return cls(config)

    @property
    def root(self) -> Path:
        """Root directory of the store."""
        return self._config.root

    @property
    def journal(self) -> Journal:
        """The operation journal."""
        return self._journal

    @property
    def undo_journal(self) -> UndoJournal:
        """The undo event journal."""
        return self._undo_journal

    # ------------------------------------------------------------------
    # Blob storage
    # ------------------------------------------------------------------

    def store_blob(self, data: bytes) -> BlobId:
        """Write blob to <root>/blobs/<hex_digest>. Idempotent."""
        bid = blob_hash(data)
        blob_path = self._config.root / "blobs" / bid.hex_digest
        if not blob_path.exists():
            blob_path.write_bytes(data)
        return bid

    def get_blob(self, blob_id: BlobId) -> bytes | None:
        """Read blob from disk. Returns None if not found."""
        blob_path = self._config.root / "blobs" / blob_id.hex_digest
        if blob_path.exists():
            return blob_path.read_bytes()
        return None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def write_metadata(self, op_count: int) -> None:
        """Write/update META.json with current operation count."""
        meta_path = self._config.root / "META.json"
        meta_path.write_text(json.dumps({
            "store_version": _STORE_VERSION,
            "operation_count": op_count,
        }))

    def read_metadata(self) -> dict[str, object]:
        """Read META.json. Returns empty dict if not found."""
        meta_path = self._config.root / "META.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text())  # type: ignore[no-any-return]
        return {}

    # ------------------------------------------------------------------
    # Dev-only: delete store
    # ------------------------------------------------------------------

    def delete_all(self) -> None:
        """Delete the entire store directory. Dev-only wipe."""
        self._journal.close()
        self._undo_journal.close()
        if self._config.root.exists():
            shutil.rmtree(self._config.root)

    @staticmethod
    def delete_store(root: Path) -> None:
        """Delete a store directory by path. Dev-only convenience."""
        if root.exists():
            shutil.rmtree(root)

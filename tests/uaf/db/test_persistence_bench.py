"""Performance benchmarks for persistence layer.

Run with: make bench
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from uaf.core.nodes import Artifact, NodeType, Paragraph, make_node_metadata
from uaf.db.graph_db import GraphDB
from uaf.db.journaled_graph_db import JournaledGraphDB
from uaf.db.store import Store, StoreConfig

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.benchmark


def _make_store(tmp_path: Path) -> Store:
    return Store.open_or_create(StoreConfig(root=tmp_path / "store"))


class TestWriteLatencyOverhead:
    """Compare write latency: JournaledGraphDB vs pure in-memory GraphDB."""

    def test_journaled_vs_inmemory_1k_creates(self, tmp_path: Path) -> None:
        n = 1000
        store = _make_store(tmp_path)
        jdb = JournaledGraphDB(store)
        gdb = GraphDB()

        # In-memory baseline
        t0 = time.perf_counter()
        for i in range(n):
            art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=f"A{i}")
            gdb.create_node(art)
        inmem_ms = (time.perf_counter() - t0) * 1000

        # Journaled
        t0 = time.perf_counter()
        for i in range(n):
            art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=f"B{i}")
            jdb.create_node(art)
        journaled_ms = (time.perf_counter() - t0) * 1000

        overhead = journaled_ms / inmem_ms if inmem_ms > 0 else float("inf")

        print(f"\n  In-memory:  {inmem_ms:.1f} ms for {n} ops")
        print(f"  Journaled:  {journaled_ms:.1f} ms for {n} ops")
        print(f"  Overhead:   {overhead:.1f}x")

        store.journal.close()


class TestReplayThroughput:
    """Measure ops/sec when replaying journals of various sizes."""

    @pytest.mark.parametrize("n", [1000, 5000])
    def test_replay_throughput(self, tmp_path: Path, n: int) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        jdb1 = JournaledGraphDB(store1)
        for i in range(n):
            art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=f"R{i}")
            jdb1.create_node(art)
        store1.journal.close()

        # Replay
        t0 = time.perf_counter()
        store2 = Store.open_or_create(config)
        _jdb2 = JournaledGraphDB(store2)
        replay_ms = (time.perf_counter() - t0) * 1000
        ops_per_sec = n / (replay_ms / 1000) if replay_ms > 0 else float("inf")

        print(f"\n  Replay {n} ops: {replay_ms:.1f} ms ({ops_per_sec:.0f} ops/sec)")

        store2.journal.close()


class TestBlobThroughput:
    """Measure blob write and read throughput."""

    def test_blob_write_throughput(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        jdb = JournaledGraphDB(store)

        sizes = [1024, 100 * 1024, 1024 * 1024]  # 1KB, 100KB, 1MB
        count = 20

        for size in sizes:
            data = bytes(range(256)) * (size // 256 + 1)
            data = data[:size]
            blobs = [data + i.to_bytes(4, "big") for i in range(count)]

            t0 = time.perf_counter()
            for blob in blobs:
                jdb.store_blob(blob)
            elapsed = time.perf_counter() - t0
            mb_per_sec = (count * size / 1024 / 1024) / elapsed if elapsed > 0 else 0

            print(f"\n  Write {count}x {size // 1024}KB blobs: {elapsed * 1000:.1f} ms"
                  f" ({mb_per_sec:.1f} MB/s)")

        store.journal.close()

    def test_blob_read_throughput(self, tmp_path: Path) -> None:
        root = tmp_path / "store"
        config = StoreConfig(root=root)

        store1 = Store.open_or_create(config)
        jdb1 = JournaledGraphDB(store1)

        size = 100 * 1024  # 100KB
        count = 50
        blob_ids = []
        for i in range(count):
            data = bytes(range(256)) * (size // 256 + 1)
            data = data[:size] + i.to_bytes(4, "big")
            bid = jdb1.store_blob(data)
            blob_ids.append(bid)
        store1.journal.close()

        # Cold read (new instance, no in-memory cache)
        store2 = Store.open_or_create(config)
        jdb2 = JournaledGraphDB(store2)

        t0 = time.perf_counter()
        for bid in blob_ids:
            jdb2.get_blob(bid)
        elapsed = time.perf_counter() - t0
        mb_per_sec = (count * size / 1024 / 1024) / elapsed if elapsed > 0 else 0

        print(f"\n  Read {count}x {size // 1024}KB blobs (cold): {elapsed * 1000:.1f} ms"
              f" ({mb_per_sec:.1f} MB/s)")

        store2.journal.close()


class TestJournalFileSize:
    """Measure bytes per operation on disk."""

    def test_journal_bytes_per_op(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        jdb = JournaledGraphDB(store)
        n = 500

        for i in range(n):
            if i % 3 == 0:
                art = Artifact(
                    meta=make_node_metadata(NodeType.ARTIFACT), title=f"Doc {i}"
                )
                jdb.create_node(art)
            else:
                para = Paragraph(
                    meta=make_node_metadata(NodeType.PARAGRAPH),
                    text=f"Paragraph content {i}",
                    style="body",
                )
                jdb.create_node(para)

        store.journal.close()
        file_size = store.journal.path.stat().st_size
        bytes_per_op = file_size / n

        print(f"\n  Journal size: {file_size:,} bytes for {n} ops")
        print(f"  Bytes/op:     {bytes_per_op:.0f}")

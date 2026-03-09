"""Performance benchmarks for JSONL persistence layer."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from uaf.core.nodes import Artifact, NodeType, Paragraph, make_node_metadata

if TYPE_CHECKING:
    from pathlib import Path


def _art(title: str = "Test") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _para(text: str = "Hello world") -> Paragraph:
    return Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)


class TestJournalBenchmarks:
    """Performance thresholds for journal operations."""

    def test_1k_writes_under_1s(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(tmp_path / "store")

        start = time.perf_counter()
        for i in range(1000):
            db.create_node(_para(f"Paragraph {i}"))
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0, f"1K writes took {elapsed:.2f}s (threshold: 1.0s)"

    def test_1k_replay_under_500ms(self, tmp_path: Path) -> None:
        from uaf.db.journal import JournaledGraphDB

        store = tmp_path / "store"
        db1 = JournaledGraphDB(store)
        for i in range(1000):
            db1.create_node(_para(f"Paragraph {i}"))

        start = time.perf_counter()
        db2 = JournaledGraphDB(store)
        elapsed = time.perf_counter() - start

        assert db2.count_nodes() == 1000
        assert elapsed < 0.5, f"1K replay took {elapsed:.3f}s (threshold: 0.5s)"

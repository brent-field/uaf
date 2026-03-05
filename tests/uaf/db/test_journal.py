"""Tests for the JSONL operation journal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uaf.core.node_id import NodeId, utc_now
from uaf.core.nodes import Artifact, Heading, NodeType, Paragraph, make_node_metadata
from uaf.core.operations import CreateNode, DeleteNode, UpdateNode
from uaf.db.journal import Journal, JournalConfig

if TYPE_CHECKING:
    from pathlib import Path


def _make_artifact(title: str = "Test") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _make_paragraph(text: str = "Hello") -> Paragraph:
    return Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text, style="body")


def _make_heading(text: str = "Title", level: int = 1) -> Heading:
    return Heading(meta=make_node_metadata(NodeType.HEADING), text=text, level=level)


def _create_op(node: object) -> CreateNode:
    return CreateNode(node=node, parent_ops=(), timestamp=utc_now())


class TestJournalAppendAndRead:
    def test_empty_journal_reads_empty(self, tmp_path: Path) -> None:
        journal = Journal(JournalConfig(path=tmp_path / "ops.jsonl"))
        assert journal.read_all() == []

    def test_append_and_read_single_op(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        op = _create_op(_make_artifact("Doc"))
        journal.append(op)
        journal.close()

        ops = journal.read_all()
        assert len(ops) == 1
        assert isinstance(ops[0], CreateNode)
        assert ops[0].node.title == "Doc"

    def test_append_and_read_multiple_ops(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        ops_in = [
            _create_op(_make_artifact("Doc1")),
            _create_op(_make_paragraph("Hello world")),
            _create_op(_make_heading("Chapter 1", level=2)),
        ]
        for op in ops_in:
            journal.append(op)
        journal.close()

        ops_out = journal.read_all()
        assert len(ops_out) == 3
        assert isinstance(ops_out[0], CreateNode)
        assert ops_out[0].node.title == "Doc1"
        assert isinstance(ops_out[1], CreateNode)
        assert ops_out[1].node.text == "Hello world"
        assert isinstance(ops_out[2], CreateNode)
        assert ops_out[2].node.level == 2

    def test_round_trip_preserves_node_ids(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        art = _make_artifact("Tracked")
        op = _create_op(art)
        journal.append(op)
        journal.close()

        ops = journal.read_all()
        assert isinstance(ops[0], CreateNode)
        assert ops[0].node.meta.id == art.meta.id

    def test_round_trip_delete_node(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        nid = NodeId.generate()
        op = DeleteNode(node_id=nid, parent_ops=(), timestamp=utc_now())
        journal.append(op)
        journal.close()

        ops = journal.read_all()
        assert len(ops) == 1
        assert isinstance(ops[0], DeleteNode)
        assert ops[0].node_id == nid

    def test_round_trip_update_node(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        art = _make_artifact("Updated")
        op = UpdateNode(node=art, parent_ops=(), timestamp=utc_now())
        journal.append(op)
        journal.close()

        ops = journal.read_all()
        assert len(ops) == 1
        assert isinstance(ops[0], UpdateNode)
        assert ops[0].node.title == "Updated"


class TestJournalCrashSafety:
    def test_partial_last_line_skipped(self, tmp_path: Path) -> None:
        """Simulate a crash mid-write: partial JSON on the last line."""
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        op = _create_op(_make_artifact("Good"))
        journal.append(op)
        journal.close()

        # Append a truncated line to simulate crash
        with path.open("a") as f:
            f.write('{"__type__":"CreateNode","node":{"__type__":"Art')

        ops = journal.read_all()
        assert len(ops) == 1
        assert isinstance(ops[0], CreateNode)
        assert ops[0].node.title == "Good"

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        op = _create_op(_make_artifact("Doc"))
        journal.append(op)
        journal.close()

        # Insert blank lines
        content = path.read_text()
        path.write_text("\n\n" + content + "\n\n")

        ops = journal.read_all()
        assert len(ops) == 1

    def test_corrupt_middle_line_skipped(self, tmp_path: Path) -> None:
        """A corrupt line in the middle is skipped; valid lines kept."""
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        op1 = _create_op(_make_artifact("First"))
        op2 = _create_op(_make_artifact("Last"))
        journal.append(op1)
        journal.close()

        # Insert corrupt line then a valid one
        with path.open("a") as f:
            f.write("NOT VALID JSON\n")

        journal2 = Journal(JournalConfig(path=path))
        journal2.append(op2)
        journal2.close()

        ops = journal.read_all()
        assert len(ops) == 2
        assert isinstance(ops[0], CreateNode)
        assert ops[0].node.title == "First"
        assert isinstance(ops[1], CreateNode)
        assert ops[1].node.title == "Last"


class TestJournalCount:
    def test_count_empty(self, tmp_path: Path) -> None:
        journal = Journal(JournalConfig(path=tmp_path / "ops.jsonl"))
        assert journal.count() == 0

    def test_count_after_appends(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        for i in range(5):
            journal.append(_create_op(_make_artifact(f"Doc{i}")))
        journal.close()

        assert journal.count() == 5

    def test_count_skips_corrupt(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))
        journal.append(_create_op(_make_artifact("Good")))
        journal.close()

        with path.open("a") as f:
            f.write("corrupt\n")

        assert journal.count() == 1


class TestJournalTruncate:
    def test_truncate_clears_journal(self, tmp_path: Path) -> None:
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        journal.append(_create_op(_make_artifact("Gone")))
        journal.close()
        assert journal.count() == 1

        journal.truncate()
        assert journal.count() == 0
        assert journal.read_all() == []

    def test_truncate_nonexistent_is_safe(self, tmp_path: Path) -> None:
        journal = Journal(JournalConfig(path=tmp_path / "nope.jsonl"))
        journal.truncate()  # Should not raise


class TestJournalFlush:
    def test_flush_on_write_true(self, tmp_path: Path) -> None:
        """With flush_on_write=True, data is readable immediately."""
        path = tmp_path / "ops.jsonl"
        journal = Journal(JournalConfig(path=path, flush_on_write=True))

        journal.append(_create_op(_make_artifact("Flushed")))
        # Read from a separate journal instance without closing the writer
        reader = Journal(JournalConfig(path=path))
        ops = reader.read_all()
        assert len(ops) == 1
        journal.close()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "dir" / "ops.jsonl"
        journal = Journal(JournalConfig(path=path))

        journal.append(_create_op(_make_artifact("Nested")))
        journal.close()

        assert path.exists()
        assert journal.count() == 1

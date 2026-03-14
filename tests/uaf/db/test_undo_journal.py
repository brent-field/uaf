"""Tests for UndoJournal — serialization/deserialization round-trip."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uaf.core.node_id import OperationId
from uaf.db.undo import UndoEvent
from uaf.db.undo_journal import UndoJournal, UndoJournalConfig

if TYPE_CHECKING:
    from pathlib import Path


def _fake_op_id(val: str = "a") -> OperationId:
    """Create a fake OperationId for testing."""
    return OperationId(hex_digest=val.ljust(64, "a"))


class TestUndoJournal:
    def test_round_trip(self, tmp_path: Path) -> None:
        """Events survive write + read_all."""
        path = tmp_path / "undo.jsonl"
        journal = UndoJournal(UndoJournalConfig(path=path))

        event = UndoEvent(
            event_type="created",
            group_id="abc123",
            op_ids=(_fake_op_id("a"), _fake_op_id("b")),
            principal_id="user1",
            artifact_id="art1",
        )
        journal.append(event)
        journal.close()

        events = journal.read_all()
        assert len(events) == 1
        assert events[0].event_type == "created"
        assert events[0].group_id == "abc123"
        assert events[0].op_ids == (_fake_op_id("a"), _fake_op_id("b"))
        assert events[0].principal_id == "user1"
        assert events[0].artifact_id == "art1"

    def test_multiple_events(self, tmp_path: Path) -> None:
        """Multiple events can be appended and read back."""
        path = tmp_path / "undo.jsonl"
        journal = UndoJournal(UndoJournalConfig(path=path))

        for etype in ("created", "undone", "redone", "redo_cleared"):
            journal.append(UndoEvent(
                event_type=etype,
                group_id=f"g_{etype}",
                op_ids=(_fake_op_id("c"),),
                principal_id="user1",
                artifact_id="art1",
            ))
        journal.close()

        events = journal.read_all()
        assert len(events) == 4
        assert [e.event_type for e in events] == [
            "created", "undone", "redone", "redo_cleared",
        ]

    def test_read_empty_file(self, tmp_path: Path) -> None:
        """Reading a nonexistent file returns empty list."""
        path = tmp_path / "nonexistent.jsonl"
        journal = UndoJournal(UndoJournalConfig(path=path))
        assert journal.read_all() == []

    def test_truncate(self, tmp_path: Path) -> None:
        """Truncate clears the journal."""
        path = tmp_path / "undo.jsonl"
        journal = UndoJournal(UndoJournalConfig(path=path))
        journal.append(UndoEvent(
            event_type="created",
            group_id="g1",
            op_ids=(_fake_op_id("a"),),
            principal_id="user1",
            artifact_id="art1",
        ))
        journal.truncate()
        assert journal.read_all() == []

    def test_corrupt_line_skipped(self, tmp_path: Path) -> None:
        """Corrupt lines are silently skipped."""
        path = tmp_path / "undo.jsonl"
        path.write_text("not valid json\n")
        journal = UndoJournal(UndoJournalConfig(path=path))
        assert journal.read_all() == []

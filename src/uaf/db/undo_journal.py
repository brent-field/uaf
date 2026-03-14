"""Append-only JSONL journal for undo events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.db.undo import UndoEvent


@dataclass(frozen=True, slots=True)
class UndoJournalConfig:
    """Configuration for the undo event journal."""

    path: Path
    flush_on_write: bool = True


class UndoJournal:
    """Append-only JSONL journal for undo events."""

    def __init__(self, config: UndoJournalConfig) -> None:
        self._config = config
        self._file: IO[str] | None = None

    @property
    def path(self) -> Path:
        """Path to the undo journal file."""
        return self._config.path

    def append(self, event: UndoEvent) -> None:
        """Serialize and append one UndoEvent as a JSONL line."""
        d = {
            "event_type": event.event_type,
            "group_id": event.group_id,
            "op_ids": [oid.hex_digest for oid in event.op_ids],
            "principal_id": event.principal_id,
            "artifact_id": event.artifact_id,
        }
        line = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
        f = self._ensure_open()
        f.write(line + "\n")
        if self._config.flush_on_write:
            f.flush()

    def read_all(self) -> list[UndoEvent]:
        """Read all events from the journal.

        Skips blank lines and corrupt/partial lines (crash safety).
        """
        from uaf.core.node_id import OperationId
        from uaf.db.undo import UndoEvent

        if not self._config.path.exists():
            return []

        events: list[UndoEvent] = []
        with self._config.path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    d = json.loads(stripped)
                    events.append(UndoEvent(
                        event_type=d["event_type"],
                        group_id=d["group_id"],
                        op_ids=tuple(
                            OperationId(hex_digest=h) for h in d["op_ids"]
                        ),
                        principal_id=d["principal_id"],
                        artifact_id=d["artifact_id"],
                    ))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return events

    def truncate(self) -> None:
        """Wipe the undo journal file (dev-only reset)."""
        self.close()
        if self._config.path.exists():
            self._config.path.write_text("")

    def close(self) -> None:
        """Close the journal file handle if open."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def _ensure_open(self) -> IO[str]:
        """Lazily open the file for appending."""
        if self._file is None:
            self._config.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._config.path.open("a", encoding="utf-8")
        return self._file

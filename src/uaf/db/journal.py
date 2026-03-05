"""Append-only JSONL journal for operation persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING

from uaf.core.operations import operation_from_dict, operation_to_dict

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.core.operations import Operation


@dataclass(frozen=True, slots=True)
class JournalConfig:
    """Configuration for the operation journal."""

    path: Path
    flush_on_write: bool = True


class Journal:
    """Append-only JSONL journal for operations.

    Each operation is serialized to one JSON line and appended to the file.
    On read, partial/corrupt last lines are skipped (crash safety).
    """

    def __init__(self, config: JournalConfig) -> None:
        self._config = config
        self._file: IO[str] | None = None

    @property
    def path(self) -> Path:
        """Path to the journal file."""
        return self._config.path

    def append(self, op: Operation) -> None:
        """Serialize and append one operation as a JSONL line."""
        line = json.dumps(
            operation_to_dict(op), separators=(",", ":"), ensure_ascii=False
        )
        f = self._ensure_open()
        f.write(line + "\n")
        if self._config.flush_on_write:
            f.flush()

    def read_all(self) -> list[Operation]:
        """Read and deserialize all operations from the journal file.

        Skips blank lines and corrupt/partial lines (crash safety).
        """
        if not self._config.path.exists():
            return []

        ops: list[Operation] = []
        with self._config.path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    d = json.loads(stripped)
                    ops.append(operation_from_dict(d))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return ops

    def count(self) -> int:
        """Number of valid operations in the journal."""
        if not self._config.path.exists():
            return 0
        n = 0
        with self._config.path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if stripped:
                    try:
                        json.loads(stripped)
                        n += 1
                    except json.JSONDecodeError:
                        continue
        return n

    def truncate(self) -> None:
        """Wipe the journal file (dev-only reset)."""
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

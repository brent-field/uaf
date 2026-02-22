"""Append-only, content-addressed operation log (Merkle DAG)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from uaf.core.errors import DuplicateOperationError, InvalidParentError
from uaf.core.operations import compute_operation_id

if TYPE_CHECKING:
    from collections.abc import Iterator

    from uaf.core.node_id import OperationId
    from uaf.core.operations import Operation


@dataclass(frozen=True, slots=True)
class LogEntry:
    """A single entry in the operation log."""

    operation_id: OperationId
    operation: Operation


class OperationLog:
    """Append-only, content-addressed operation log.

    Internally stores entries in append order and maintains
    an index for O(1) lookup by OperationId.
    """

    def __init__(self) -> None:
        self._entries: list[LogEntry] = []
        self._index: dict[OperationId, LogEntry] = {}
        self._referenced_as_parent: set[OperationId] = set()

    def append(self, op: Operation) -> OperationId:
        """Validate parent refs, compute content hash, and store the operation.

        Returns the OperationId (content hash).
        Raises DuplicateOperationError if the exact operation already exists.
        Raises InvalidParentError if any parent_ops reference non-existent entries.
        """
        # Validate parent references
        for parent_id in op.parent_ops:
            if parent_id not in self._index:
                msg = f"Parent operation not found: {parent_id}"
                raise InvalidParentError(msg)

        op_id = compute_operation_id(op)

        if op_id in self._index:
            raise DuplicateOperationError(f"Operation already exists: {op_id}")

        entry = LogEntry(operation_id=op_id, operation=op)
        self._entries.append(entry)
        self._index[op_id] = entry

        # Track which ops are referenced as parents (for head_ids)
        for parent_id in op.parent_ops:
            self._referenced_as_parent.add(parent_id)

        return op_id

    def get(self, op_id: OperationId) -> LogEntry | None:
        """O(1) lookup by content hash. Returns None if not found."""
        return self._index.get(op_id)

    @property
    def head_ids(self) -> frozenset[OperationId]:
        """DAG leaves — operations not referenced as parents by any other operation."""
        all_ids = set(self._index.keys())
        return frozenset(all_ids - self._referenced_as_parent)

    def entries_since(self, op_id: OperationId) -> list[LogEntry]:
        """All entries appended after the given operation (for sync)."""
        # Find the index of the given entry
        entry = self._index.get(op_id)
        if entry is None:
            return list(self._entries)  # If not found, return all

        idx = self._entries.index(entry)
        return self._entries[idx + 1 :]

    def ancestors(self, op_id: OperationId) -> list[LogEntry]:
        """Walk the DAG backwards from op_id to genesis, returning all ancestors."""
        result: list[LogEntry] = []
        visited: set[OperationId] = set()
        stack = [op_id]

        while stack:
            current_id = stack.pop()
            if current_id in visited:
                continue
            visited.add(current_id)

            entry = self._index.get(current_id)
            if entry is None:
                continue

            result.append(entry)
            for parent_id in entry.operation.parent_ops:
                if parent_id not in visited:
                    stack.append(parent_id)

        return result

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[LogEntry]:
        return iter(self._entries)

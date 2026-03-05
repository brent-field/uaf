"""Format handlers — import/export/compare protocols and ComparisonResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.core.node_id import NodeId
    from uaf.db.graph_db import GraphDB
    from uaf.db.journaled_graph_db import JournaledGraphDB


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Result of comparing an original file with a rebuilt export."""

    is_equivalent: bool
    differences: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    similarity_score: float = 1.0


class FormatHandler(Protocol):
    """Protocol for import/export of a file format."""

    def import_file(self, path: Path, db: GraphDB | JournaledGraphDB) -> NodeId:
        """Import a file into the graph. Returns the root Artifact ID."""
        ...

    def export_file(
        self, db: GraphDB | JournaledGraphDB, root_id: NodeId, path: Path
    ) -> None:
        """Export an artifact from the graph to a file."""
        ...


class FormatComparator(Protocol):
    """Protocol for comparing original vs. rebuilt files."""

    def compare(self, original: Path, rebuilt: Path) -> ComparisonResult:
        """Compare two files, ignoring allowed differences."""
        ...


__all__ = [
    "ComparisonResult",
    "FormatComparator",
    "FormatHandler",
]

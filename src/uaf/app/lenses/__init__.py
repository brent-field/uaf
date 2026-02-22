"""Lens protocol — view protocol, LensView output, and LensRegistry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from uaf.app.lenses.actions import LensAction
    from uaf.core.node_id import NodeId
    from uaf.core.nodes import NodeType
    from uaf.security.secure_graph_db import SecureGraphDB, Session


@dataclass(frozen=True, slots=True)
class LensView:
    """Rendered output from a Lens."""

    lens_type: str
    artifact_id: NodeId
    title: str
    content: str
    content_type: str
    node_count: int
    rendered_at: datetime


@runtime_checkable
class Lens(Protocol):
    """Protocol for artifact views."""

    @property
    def lens_type(self) -> str:
        """Identifier for this lens (e.g., 'doc', 'grid')."""
        ...

    @property
    def supported_node_types(self) -> frozenset[NodeType]:
        """Node types this lens can render."""
        ...

    def render(
        self, db: SecureGraphDB, session: Session, artifact_id: NodeId
    ) -> LensView:
        """Render an artifact into a view."""
        ...

    def apply_action(
        self,
        db: SecureGraphDB,
        session: Session,
        artifact_id: NodeId,
        action: LensAction,
    ) -> None:
        """Apply a user action to the graph."""
        ...


class LensRegistry:
    """Maps lens type strings to Lens instances."""

    def __init__(self) -> None:
        self._lenses: dict[str, Lens] = {}

    def register(self, lens: Lens) -> None:
        """Register a lens instance."""
        self._lenses[lens.lens_type] = lens

    def get(self, lens_type: str) -> Lens | None:
        """Get a lens by type string."""
        return self._lenses.get(lens_type)

    def available(self) -> list[str]:
        """Return sorted list of registered lens type strings."""
        return sorted(self._lenses)

    def for_node_type(self, node_type: NodeType) -> list[Lens]:
        """Return all lenses that support a given node type."""
        return [
            lens for lens in self._lenses.values()
            if node_type in lens.supported_node_types
        ]

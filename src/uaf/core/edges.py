"""Edge model — Edge, EdgeType, and edge property types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from uaf.core.node_id import EdgeId, NodeId


@unique
class EdgeType(Enum):
    """Enumeration of all edge types in the graph."""

    CONTAINS = "contains"
    REFERENCES = "references"
    DEPENDS_ON = "depends_on"
    COMPLIES_WITH = "complies_with"
    FOLLOWS = "follows"
    LINKED_TO = "linked_to"
    OWNED_BY = "owned_by"
    GRANTS_ROLE = "grants_role"


EdgePropertyValue = str | int | float | bool


@dataclass(frozen=True, slots=True)
class Edge:
    """A typed, directed relationship between two nodes."""

    id: EdgeId
    source: NodeId
    target: NodeId
    edge_type: EdgeType
    created_at: datetime
    properties: tuple[tuple[str, EdgePropertyValue], ...] = ()

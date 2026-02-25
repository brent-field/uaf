"""Shared helpers for PDF fidelity tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from uaf.app.formats.pdf_format import PdfHandler
from uaf.db.graph_db import GraphDB

if TYPE_CHECKING:
    from uaf.core.node_id import NodeId

_FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "pdf"


def _import_pdf(filename: str) -> tuple[GraphDB, NodeId, list[Any]]:
    """Import a fixture PDF and return (db, root_id, children)."""
    db = GraphDB()
    handler = PdfHandler()
    root_id = handler.import_file(_FIXTURES / filename, db)
    children: list[Any] = db.get_children(root_id)
    return db, root_id, children


def _find_block(children: list[Any], substring: str) -> Any:
    """Find the first child whose text contains the given substring."""
    for child in children:
        text: str = getattr(child, "text", "") or getattr(child, "source", "")
        if substring in text:
            return child
    msg = f"No block containing {substring!r}"
    raise ValueError(msg)

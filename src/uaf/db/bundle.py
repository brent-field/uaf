"""UAF bundle export/import — .uaf zip format containing journal + blobs + manifest."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from uaf.core.node_id import utc_now
from uaf.core.operations import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    MoveNode,
    ReorderChildren,
    UpdateNode,
    operation_from_dict,
    operation_to_dict,
)
from uaf.core.serialization import canonical_json

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.core.node_id import BlobId, NodeId
    from uaf.db.graph_db import GraphDB
    from uaf.db.journaled_graph_db import JournaledGraphDB


# ---------------------------------------------------------------------------
# Manifest dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleArtifactInfo:
    """Metadata for one artifact in a bundle."""

    id: str
    title: str


@dataclass(frozen=True)
class BundleManifest:
    """Top-level manifest for a .uaf bundle."""

    version: int
    mode: str  # "full" or "snapshot"
    artifacts: list[BundleArtifactInfo]
    created_at: str


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_bundle(
    db: GraphDB | JournaledGraphDB,
    artifact_ids: list[NodeId],
    path: Path,
    *,
    snapshot: bool = False,
) -> None:
    """Export one or more artifacts as a .uaf bundle (zip).

    If snapshot=True, generate synthetic CreateNode/CreateEdge ops representing
    the current state only. Otherwise, export the full operation history filtered
    to the subtree.
    """
    from uaf.core.nodes import Artifact

    # Collect all NodeIds in the subtrees
    all_node_ids: set[NodeId] = set()
    for aid in artifact_ids:
        all_node_ids.update(db.descendants(aid))

    # Collect all EdgeIds in the subtrees
    all_edge_ids: set[Any] = set()
    for nid in all_node_ids:
        for edge in db.get_edges_from(nid):
            all_edge_ids.add(edge.id)

    # Build manifest artifact info
    artifacts_info: list[BundleArtifactInfo] = []
    for aid in artifact_ids:
        art = db.get_node(aid)
        title = art.title if isinstance(art, Artifact) else str(aid)
        artifacts_info.append(BundleArtifactInfo(id=str(aid), title=title))

    manifest = BundleManifest(
        version=1,
        mode="snapshot" if snapshot else "full",
        artifacts=artifacts_info,
        created_at=utc_now().isoformat(),
    )

    # Build journal lines
    if snapshot:
        journal_lines = _build_snapshot_journal(db, all_node_ids, all_edge_ids)
    else:
        journal_lines = _build_full_journal(db, all_node_ids, all_edge_ids)

    # Collect blob references from nodes in the subtree
    blob_ids = _collect_blob_ids(db, all_node_ids)

    # Write zip
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Manifest
        manifest_dict = {
            "version": manifest.version,
            "mode": manifest.mode,
            "artifacts": [{"id": a.id, "title": a.title} for a in manifest.artifacts],
            "created_at": manifest.created_at,
        }
        zf.writestr("manifest.json", json.dumps(manifest_dict, indent=2))

        # Journal
        zf.writestr("journal.jsonl", "\n".join(journal_lines) + "\n" if journal_lines else "")

        # Blobs
        for bid in blob_ids:
            data = db.get_blob(bid)
            if data is not None:
                zf.writestr(f"blobs/{bid.hex_digest}", data)


def _build_full_journal(
    db: GraphDB | JournaledGraphDB,
    node_ids: set[NodeId],
    edge_ids: set[Any],
) -> list[str]:
    """Filter the operation log to only ops affecting the given subtree."""
    lines: list[str] = []
    for entry in db._log:
        op = entry.operation
        if _op_in_subtree(op, node_ids, edge_ids):
            d = operation_to_dict(op)
            # Strip parent_ops references since they may refer to ops outside the subtree
            d["parent_ops"] = []
            lines.append(canonical_json(d).decode("utf-8"))
    return lines


def _build_snapshot_journal(
    db: GraphDB | JournaledGraphDB,
    node_ids: set[NodeId],
    edge_ids: set[Any],
) -> list[str]:
    """Build synthetic CreateNode + CreateEdge ops for current state."""
    lines: list[str] = []
    now = utc_now()

    # First, create all nodes
    for nid in node_ids:
        node = db.get_node(nid)
        if node is not None:
            cn_op = CreateNode(node=node, parent_ops=(), timestamp=now)
            lines.append(canonical_json(operation_to_dict(cn_op)).decode("utf-8"))

    # Then, create all edges
    for nid in node_ids:
        for edge in db.get_edges_from(nid):
            if edge.id in edge_ids:
                ce_op = CreateEdge(edge=edge, parent_ops=(), timestamp=now)
                lines.append(canonical_json(operation_to_dict(ce_op)).decode("utf-8"))

    # Preserve child ordering via ReorderChildren
    for nid in node_ids:
        children = db.get_children(nid)
        if children:
            child_ids = tuple(c.meta.id for c in children)
            ro_op = ReorderChildren(
                parent_id=nid, new_order=child_ids, parent_ops=(), timestamp=now,
            )
            lines.append(canonical_json(operation_to_dict(ro_op)).decode("utf-8"))

    return lines


def _op_in_subtree(
    op: Any,
    node_ids: set[NodeId],
    edge_ids: set[Any],
) -> bool:
    """Check if an operation affects a node or edge in the given subtree."""
    match op:
        case CreateNode(node=node) | UpdateNode(node=node):
            return node.meta.id in node_ids
        case DeleteNode(node_id=nid):
            return nid in node_ids
        case CreateEdge(edge=edge):
            return edge.id in edge_ids
        case DeleteEdge(edge_id=eid):
            return eid in edge_ids
        case MoveNode(node_id=nid):
            return nid in node_ids
        case ReorderChildren(parent_id=pid):
            return pid in node_ids
        case _:
            return False


def _collect_blob_ids(db: GraphDB | JournaledGraphDB, node_ids: set[NodeId]) -> list[BlobId]:
    """Scan nodes for blob: URI references and return their BlobIds."""
    from uaf.core.node_id import BlobId

    blob_ids: list[BlobId] = []
    seen: set[str] = set()

    for nid in node_ids:
        node = db.get_node(nid)
        if node is None:
            continue
        # Check if node has a uri field with blob: prefix
        uri = getattr(node, "uri", None)
        if uri and isinstance(uri, str) and uri.startswith("blob:"):
            hex_digest = uri[5:]
            if hex_digest not in seen:
                seen.add(hex_digest)
                blob_ids.append(BlobId(hex_digest=hex_digest))

    return blob_ids


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_bundle(db: GraphDB | JournaledGraphDB, path: Path) -> list[NodeId]:
    """Import a .uaf bundle into the given GraphDB. Returns imported artifact NodeIds."""
    import uuid

    from uaf.core.node_id import NodeId

    if not zipfile.is_zipfile(path):
        msg = f"Not a valid zip file: {path}"
        raise ValueError(msg)

    with zipfile.ZipFile(path, "r") as zf:
        # Read manifest
        if "manifest.json" not in zf.namelist():
            msg = "Bundle missing manifest.json"
            raise ValueError(msg)

        manifest = json.loads(zf.read("manifest.json"))
        artifact_ids = [
            NodeId(value=uuid.UUID(a["id"])) for a in manifest["artifacts"]
        ]

        # Import blobs
        for name in zf.namelist():
            if name.startswith("blobs/") and name != "blobs/":
                data = zf.read(name)
                db.store_blob(data)

        # Replay journal (skip duplicate ops that already exist in the db)
        if "journal.jsonl" in zf.namelist():
            from uaf.core.errors import DuplicateOperationError

            journal_text = zf.read("journal.jsonl").decode("utf-8")
            for line in journal_text.strip().splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                d = json.loads(stripped)
                op = operation_from_dict(d)
                try:
                    db.apply(op)
                except DuplicateOperationError:
                    continue  # Op already exists, skip

    return artifact_ids

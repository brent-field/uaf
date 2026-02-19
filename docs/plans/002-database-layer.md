# UAF Database Layer — Implementation Plan

**Version:** 1.1
**Date:** 2026-02-17
**Scope:** Core data model (`src/uaf/core/`) + Database layer (`src/uaf/db/`)

> **Rename:** The project is now **Universal Artifact Format (UAF)**. The Python package
> is `uaf` (replacing `udf`). All imports use `from uaf.core import ...`. The term
> "Artifact" replaces "Document" as the top-level container — a chess game, spreadsheet,
> CAD model, and text document are all Artifacts.

---

## 1. Context

We're building the foundational data layer for the **Universal Artifact Format** — a
graph-based, AI-native knowledge protocol. After extensive research into graph databases,
RDF stores, block tables (Notion-style), CRDTs, and real-world architectures (Figma,
Datomic, git), we chose an **event-sourced, content-addressed operation DAG** architecture.

**Why not a block table or graph DB?** Every successful collaborative system (Figma, Notion,
Linear) separates its source of truth from its query engine. Our source of truth is an
append-only operation log (like git); current state is a materialized projection with EAVT
indexes (like Datomic). This gives us: history for free, content-addressed integrity, natural
CRDT sync (operations are the merge unit), and independently testable components.

**Why EAVT indexes?** Four covering indexes (Entity-Attribute-Value-Transaction) give us O(log n)
queries for every access pattern: "all attributes of node X" (EAVT), "all nodes with
attribute Y" (AEVT), "nodes where attr=val" (AVET), "all references to node X" (VAET).

**Terminology:** The top-level container for any data is called an **Artifact**. A chess game,
a spreadsheet, a CAD model, and a text document are all Artifacts — root nodes that a Lens
renders.

**Requirement: Multi-user concurrent editing.** The architecture MUST support multiple
people editing the same artifact simultaneously. This is an explicit design goal, not a
nice-to-have. The event-sourced operation DAG makes this possible — operations from
different users are independent DAG entries that merge naturally (same foundation as git,
Figma, and CRDTs). V1 builds the single-user foundation; the CRDT sync phase (Appendix B)
adds the merge protocol; the application layer adds WebSocket push for real-time updates.
Every architectural decision in this plan is evaluated against this requirement.

**V1 scope:** Purely in-memory. No persistence, no CRDT sync, no network. The goal is a
correct, well-tested, well-typed foundation that everything else builds on.

**Software:** 100% FOSS. Python (PSF license), sortedcontainers (Apache 2.0), stdlib only.

### Why Python (and when to move beyond it)

Python 3.13+ is the right choice for V1:
- Fastest path to a working VC demo
- Mypy strict gives type safety comparable to Go/TypeScript
- The architecture **isolates hot paths** (hashing, indexing, CRDT merge) into self-contained
  modules that can be rewritten in Rust (via PyO3) without touching the rest
- This is exactly what the best data tools do: Polars (Rust core + Python API), DuckDB
  (C++ core + Python API), Loro (Rust CRDT + Python bindings), Automerge (Rust + bindings)

The long-term ideal is **Rust core + Python bindings**. But building Rust first would 3-5x
development time with no user-visible benefit until we hit scale. Start Python, profile,
push hot paths to Rust when (not if) they become bottlenecks.

---

## 2. Architecture

```
  Mutation path                          Query path
  ──────────                             ──────────
  Operation                              QueryEngine
      │                                      │
      ▼                                      ▼
  OperationLog ──► StateMaterializer ──► MaterializedState
  (append-only     (replays ops)         (nodes, edges,
   Merkle DAG)          │                 children_order)
                        ▼
                    EAVTIndex
                    (4 covering indexes
                     via sortedcontainers)
```

**GraphDB** is the facade composing all components. External code uses GraphDB; internal
components are independently testable.

### Ownership Model

Every node has an optional `owner` field in `NodeMetadata`. The AVET index makes
"find all nodes owned by user X" a fast prefix scan. For richer ownership (shared,
delegated), an `OWNED_BY` edge type connects nodes to owner entities.

### Layout Preservation (PDF and visual formats)

Nodes carry an optional `layout: LayoutHint | None` field in `NodeMetadata`:

```
LayoutHint: page, x, y, width, height, font_family, font_size,
            font_weight, font_style, color, reading_order
```

When importing PDFs or slides, `pdfplumber`/`unstructured` extract this layout metadata.
Lenses can use it to render closer to the original, or ignore it and re-flow. This is
not lossy — the *layout data* is preserved. What's inherently lossy is *semantic structure*
(PDF doesn't know "this is a heading"), but heuristics (font size, position, whitespace)
can infer much of it.

### MCP Server Interface (Future — `src/uaf/app/`)

The GraphDB API maps directly to MCP tools, enabling AI agents to navigate the graph:

```
MCP Tool                        → GraphDB Method
────────                          ──────────────
create_artifact(title)          → db.create_node(Artifact(...))
add_child(parent_id, node)      → db.create_node(...) + db.create_edge(CONTAINS)
get_node(node_id)               → db.get_node(node_id)
get_children(node_id)           → db.get_children(node_id)
find_by_type("Task")            → db.find_by_type(NodeType.TASK)
get_references_to(node_id)      → db.get_references_to(node_id)
search(attribute, value)        → db.find_by_attribute(attribute, value)
get_history(node_id)            → db.get_history(node_id)
```

This is the Graph-RAG vision: AI agents **navigate** the graph (follow edges, query by
type, find references) rather than "reading documents." The EAVT indexes make every query
fast regardless of graph size. A thin MCP server wrapper around GraphDB is a natural
next step after V1.

### Binary / Blob Storage

Nodes like `Image` carry a `uri: str`, but where do the actual bytes live? A content-
addressed blob store, separate from the operation log.

```
BlobId = SHA-256 hash of raw bytes
BlobStore (V1) = dict[BlobId, bytes]
```

When importing an image, the binary is hashed and stored in the `BlobStore`. The `Image`
node's `uri` becomes `blob:<hex-digest>`. Operations reference blobs by hash — the
operation log stays small (no inline binary data). Blobs are immutable and deduplicated
by content.

**V1 scope:** In-memory `dict`. Future: filesystem or S3-backed, same interface.

**Impact on plan:** Add `BlobId` to Phase 1 (identifiers), `BlobStore` to Phase 11
(GraphDB manages it), and blob round-trip tests to Phase 13.

### Graph Constraints (Valid Edges)

Not all edges are semantically valid. A `Cell` should not be a child of a `Paragraph`.
We define a constraint table enforced by `GraphDB.create_edge()`:

```
EdgeType       Valid Source → Target
─────────      ──────────────────────
CONTAINS       Artifact → any node type
               Sheet → Cell, FormulaCell
               Slide → Shape, TextBlock, Image
               (parent must be a "container" type)
REFERENCES     any → any (transclusion)
DEPENDS_ON     FormulaCell → Cell (formula dependencies)
FOLLOWS        any → any (sequencing, e.g., chess moves)
LINKED_TO      any → any (user-defined links)
OWNED_BY       any → any (ownership relationships)
COMPLIES_WITH  any → any (regulatory/schema compliance)
GRANTS_ROLE    ArtifactACL → any (permission grants, see 003-security-layer.md)
```

**Enforcement:** `create_edge()` validates against this table before appending the
operation. Invalid edges raise `InvalidEdgeError`. The constraint table is a frozen
dict — easy to extend when new node types are added.

**Design tradeoff:** Strict constraints catch bugs early but reduce flexibility. We
start strict for `CONTAINS` (structural tree integrity matters) and permissive for
relationship edges (`REFERENCES`, `LINKED_TO`).

### Error Handling Philosophy

Fail-fast with typed exceptions. No silent `None` returns for operations that should
succeed. Queries that may legitimately find nothing return `None` or empty lists.

```
UAFError (base)
├── NodeNotFoundError       — get_node / update / delete on missing ID
├── EdgeNotFoundError       — get_edge / delete on missing ID
├── InvalidEdgeError        — constraint violation (see above)
├── DuplicateOperationError — appending an op with an existing hash
├── InvalidParentError      — operation references non-existent parent ops
└── SerializationError      — unknown __type__, corrupt data
```

**Principle:** Mutation methods (`apply`, `create_node`, `create_edge`) raise on any
error. Query methods (`get_node`, `get_children`, `find_by_type`) return `None` or
`[]` for "not found" — these are expected outcomes, not errors.

**Impact on plan:** Add `src/uaf/core/errors.py` in Phase 1 (alongside identifiers).
All subsequent phases import from it.

### Query Scope (Multi-Artifact)

The EAVT index is **global** — it spans all artifacts in the graph. `find_by_type(TASK)`
returns every Task node across every artifact. This is by design: cross-artifact queries
("find all my tasks," "find all references to node X") are first-class operations.

For per-artifact scoping, callers filter by walking the containment tree:
```python
all_tasks = db.find_by_type(NodeType.TASK)
my_artifact_nodes = set(db.descendants(artifact_id))
artifact_tasks = [t for t in all_tasks if t.meta.id in my_artifact_nodes]
```

A convenience method `db.find_in_artifact(artifact_id, NodeType.TASK)` can wrap this
pattern. The EAVT index makes the initial query fast; the containment tree walk is the
filtering step.

### Enterprise Scaling Profile

**Write path** — what happens when a user edits a node:

```
1. Hash operation (SHA-256)              ~1 μs
2. Append to operation log (list + dict) ~0.1 μs
3. Materialize state (dict insert)       ~0.5 μs
4. Extract datoms from node (~5 attrs)   ~2 μs
5. Insert into 4 EAVT indexes           ~40 μs  (O(log n) × 4 × 5 attrs)
                                         ────────
Total per write:                         ~50 μs  (~20,000 writes/sec)
```

At 1M nodes in the index, `SortedList.add()` does ~20 comparisons (log₂ 1M ≈ 20).
At 10M, ~23 comparisons. Write latency grows logarithmically — near-constant in practice.

**Read path** — what enterprise users experience:

| Operation | Complexity | Latency (1K nodes) | Latency (100K nodes) | Latency (1M nodes) |
|-----------|-----------|-------------------|---------------------|-------------------|
| Get single node | O(1) dict lookup | <1 μs | <1 μs | <1 μs |
| Get children (50 children) | O(k) list + dict | ~5 μs | ~5 μs | ~5 μs |
| Render 100-page doc (~500 nodes) | O(n) tree walk | ~50 μs | ~50 μs | ~50 μs |
| Find by type (e.g., all Tasks) | O(log n + k) prefix scan | ~10 μs | ~20 μs | ~30 μs |
| Reverse reference lookup | O(log n + k) VAET scan | ~10 μs | ~20 μs | ~30 μs |
| Find by attribute=value | O(log n + k) AVET scan | ~10 μs | ~20 μs | ~30 μs |
| Full-text search | Not in V1 (needs FTS index) | — | — | — |
| Spreadsheet SUM over 1M cells | O(n) scan (no columnar index) | — | — | ~100 ms |

**Key takeaway:** Reads that follow the graph structure (get node, get children, render
document) are **near-instant at any scale** because they're dict lookups. Indexed queries
(find by type, reverse refs) grow as O(log n) — barely noticeable. The one weak spot is
analytical queries over large flat collections (SUM over 1M cells) — this is where
type-specialized storage (DuckDB) becomes necessary at scale.

**Memory footprint:**

| Graph Size | Nodes | Datoms (~5 attrs/node) | 4 EAVT indexes | Total RAM |
|-----------|-------|----------------------|----------------|-----------|
| Small | 1K | 5K | ~4 MB | ~5 MB |
| Medium | 100K | 500K | ~400 MB | ~500 MB |
| Large | 1M | 5M | ~4 GB | ~5 GB |
| Enterprise | 10M | 50M | ~40 GB | Needs persistence |

V1 (in-memory) comfortably handles 100K nodes on any machine. At 1M nodes, it needs a
beefy server (~8 GB). Beyond 1M, the persistence layer (SQLite/PostgreSQL-backed indexes)
is required — the in-memory architecture becomes the hot cache in front of disk.

**Optimization path to enterprise scale:**

| Scale | Solution | Impact |
|-------|----------|--------|
| 100K nodes | V1 as-is | Everything <1 ms |
| 1M nodes | String interning for repeated values ("node_type", "text") | 2-3x memory reduction |
| 1M nodes | Single datom storage + 4 index references (not 4 copies) | 3x memory reduction |
| 10M nodes | Persistence layer (SQLite for local, PostgreSQL for server) | Bounded memory |
| 10M+ nodes | Rust core via PyO3 for hashing + indexes | 10-50x throughput |
| 100M nodes | Sharded PostgreSQL (Notion model) | Horizontal scaling |
| Analytical | DuckDB for columnar queries on sheets/data | 1M-row SUM in <10 ms |

### Usage Example — What Building on GraphDB Looks Like

```python
from uaf.db import GraphDB
from uaf.core import (
    Artifact, Heading, Paragraph, make_node_metadata,
    NodeType, EdgeType, Edge, EdgeId,
)

# Create a graph database (in-memory)
db = GraphDB()

# Create an artifact (top-level container)
meta = make_node_metadata(NodeType.ARTIFACT)
doc = Artifact(meta=meta, title="Quarterly Report")
doc_id = db.create_node(doc)

# Add children
h1 = Heading(meta=make_node_metadata(NodeType.HEADING), text="Summary", level=1)
h1_id = db.create_node(h1)
db.create_edge(Edge(
    id=EdgeId.generate(), source=doc_id, target=h1_id,
    edge_type=EdgeType.CONTAINS, created_at=utc_now(), properties=(),
))

p1 = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text="Revenue grew 15%.", style="body")
p1_id = db.create_node(p1)
db.create_edge(Edge(
    id=EdgeId.generate(), source=doc_id, target=p1_id,
    edge_type=EdgeType.CONTAINS, created_at=utc_now(), properties=(),
))

# Query the graph
children = db.get_children(doc_id)         # [Heading, Paragraph] in order
headings = db.find_by_type(NodeType.HEADING)  # [Heading] across all artifacts
refs = db.get_references_to(p1_id)         # [] (nothing references this yet)
history = db.get_history(doc_id)           # [CreateNode op]

# Transclusion — reference the same paragraph from another artifact
report2 = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Board Deck")
r2_id = db.create_node(report2)
db.create_edge(Edge(
    id=EdgeId.generate(), source=r2_id, target=p1_id,
    edge_type=EdgeType.REFERENCES, created_at=utc_now(), properties=(),
))
# p1 now appears in both artifacts; editing it updates both
```

This is the API that Lenses and MCP tools build on. All queries are O(1) or O(log n).

---

## 3. Dependencies

```toml
# pyproject.toml additions
[project]
dependencies = ["sortedcontainers>=2.4"]

[dependency-groups]
dev = [...existing..., "sortedcontainers-stubs>=2.4"]
```

All dependencies are FOSS (Apache 2.0 / MIT).

---

## 4. Implementation Phases

Each phase produces a green `make check` (ruff + mypy strict + pytest).

### Phase 0: Project Rename — `udf` → `uaf`

Rename the Python package from `udf` to `uaf`:
- `src/udf/` → `src/uaf/` (all subdirectories: core, db, security, app)
- `tests/` → `tests/uaf/` (mirror new source structure)
- Update `pyproject.toml`: project name, package path, mypy path, pytest pythonpath
- Update `CLAUDE.md`, `README.md`, `docs/architecture.md`
- Update all `__init__.py` docstrings
- Add `sortedcontainers>=2.4` to dependencies, `sortedcontainers-stubs>=2.4` to dev

**Verification:** `make check` passes (no source code yet, just structure + config).

### Phase 1: Core Identifiers + Errors — `src/uaf/core/node_id.py`, `src/uaf/core/errors.py`

| Type | Description |
|------|-------------|
| `NodeId` | Frozen dataclass wrapping `uuid.UUID`, with `generate()` classmethod |
| `EdgeId` | Same pattern as NodeId |
| `OperationId` | Frozen dataclass wrapping a 64-char hex string (SHA-256 digest) |
| `BlobId` | Frozen dataclass wrapping a 64-char hex string (SHA-256 of raw bytes) |
| `utc_now()` | Returns timezone-aware UTC datetime |

**Error hierarchy** (`src/uaf/core/errors.py`):
```
UAFError (base)
├── NodeNotFoundError
├── EdgeNotFoundError
├── InvalidEdgeError
├── DuplicateOperationError
├── InvalidParentError
├── SerializationError
├── PermissionDeniedError    ← used by security layer
└── AuthenticationError      ← used by security layer
```

**Design:** Wrapping UUID in a dataclass gives type safety — you cannot pass a `NodeId`
where an `EdgeId` is expected. `OperationId` is a content hash, NOT a UUID. `BlobId`
follows the same pattern as `OperationId` (SHA-256 hex digest of raw binary data).
Errors are defined in core so both the db and security layers can use them without
circular dependencies.

**Tests:** `tests/uaf/core/test_node_id.py` (~12 tests: uniqueness, hashability, immutability,
validation of hex strings, BlobId construction, utc_now timezone awareness)

---

### Phase 2: Node Types — `src/uaf/core/nodes.py`

**NodeType enum:**
```
ARTIFACT, PARAGRAPH, HEADING, TEXT_BLOCK, CELL, FORMULA_CELL, SHEET,
CODE_BLOCK, TASK, SLIDE, SHAPE, IMAGE, ARTIFACT_ACL, RAW
```

**NodeMetadata** — frozen dataclass shared by all nodes:
```
id: NodeId, node_type: NodeType, created_at: datetime,
updated_at: datetime, owner: str | None = None,
layout: LayoutHint | None = None
```

**Concrete node types** — frozen dataclasses with `meta: NodeMetadata` + typed fields:

| Type | Key Fields | Used By |
|------|-----------|---------|
| `Artifact` | `title: str` | All Lenses (top-level container) |
| `Paragraph` | `text: str, style: str` | DocLens |
| `Heading` | `text: str, level: int` | DocLens |
| `TextBlock` | `text: str, format: str` | DocLens, WikiLens |
| `Cell` | `value: str \| int \| float \| bool \| None, row: int, col: int` | GridLens |
| `FormulaCell` | `formula: str, cached_value: ..., row: int, col: int` | GridLens |
| `Sheet` | `title: str, rows: int, cols: int` | GridLens |
| `CodeBlock` | `source: str, language: str` | CodeLens |
| `Task` | `title: str, completed: bool, due_date: ...` | FlowLens |
| `Slide` | `title: str, order: int` | SlideLens |
| `Shape` | `shape_type: str, x, y, width, height` | DrawLens |
| `Image` | `uri: str, alt_text: str, width, height` | All visual Lenses |
| `ArtifactACL` | `default_role: str \| None, public_read: bool` | Security layer (see `003-security-layer.md`) |
| `RawNode` | `raw: dict, original_type: str` | Schema evolution fallback (see Phase 4) |

**Union type:** `type NodeData = Artifact | Paragraph | Heading | ...` (Python 3.12+ syntax)

**Helper:** `make_node_metadata(node_type, owner=None) -> NodeMetadata`

**Design:** Composition over inheritance — each node has `meta: NodeMetadata` rather than
inheriting from a base class. Frozen dataclasses don't support inheritance well, and
composition keeps the type union clean for `match` statements.

**Tests:** `tests/uaf/core/test_nodes.py` (~20 tests: construction, immutability, union type
checking, match exhaustiveness, owner field)

---

### Phase 3: Edge Model — `src/uaf/core/edges.py`

**EdgeType enum:**
```
CONTAINS, REFERENCES, DEPENDS_ON, COMPLIES_WITH, FOLLOWS, LINKED_TO, OWNED_BY, GRANTS_ROLE
```

**Edge** — frozen dataclass:
```
id: EdgeId, source: NodeId, target: NodeId, edge_type: EdgeType,
created_at: datetime, properties: tuple[tuple[str, str | int | float | bool], ...]
```

**Design:** `CONTAINS` edges form the structural tree (artifact → children). `REFERENCES`
edges implement transclusion. `OWNED_BY` edges model rich ownership. Properties are a
frozen tuple of pairs (not a dict) to preserve immutability.

**Tests:** `tests/uaf/core/test_edges.py` (~8 tests)

---

### Phase 4: Serialization — `src/uaf/core/serialization.py`

| Function | Purpose |
|----------|---------|
| `node_to_dict(node) -> dict` | Serialize any NodeData with `__type__` discriminator |
| `node_from_dict(d) -> NodeData` | Deserialize using `__type__` to select class |
| `edge_to_dict` / `edge_from_dict` | Same for edges |
| `canonical_json(data) -> bytes` | Sorted keys, no whitespace, UTF-8 |
| `content_hash(data) -> OperationId` | SHA-256 of canonical JSON |

**Critical property:** `node_from_dict(node_to_dict(x)) == x` for ALL node types.

**Schema evolution:** Every serialized dict includes `__schema_version__: int` (starting
at 1). When `node_from_dict` encounters an unknown `__type__`, it returns a `RawNode`
frozen dataclass that preserves the original dict verbatim:
```
RawNode(meta: NodeMetadata, raw: dict[str, Any], original_type: str)
```
This means old code can load new data without crashing — it just can't interpret the
unknown fields. When we add a new `NodeType` in v2, old operation logs still replay
(new types become `RawNode` until the code is updated). Migration functions can be
registered per version bump: `register_migration(from_version=1, to_version=2, fn)`.

**Tests:** `tests/uaf/core/test_serialization.py` (~18 tests: round-trips for every type,
deterministic hashing, hash uniqueness, unknown type → RawNode, schema version round-trip)

---

### Phase 5: Operation Types — `src/uaf/core/operations.py`

| Operation | Fields | Purpose |
|-----------|--------|---------|
| `CreateNode` | `node: NodeData` | Add a node to the graph |
| `UpdateNode` | `node: NodeData` (full new state) | Replace node data |
| `DeleteNode` | `node_id: NodeId` | Mark node as deleted |
| `CreateEdge` | `edge: Edge` | Add an edge |
| `DeleteEdge` | `edge_id: EdgeId` | Remove an edge |
| `MoveNode` | `node_id, new_parent_id` | Re-parent in containment tree |
| `ReorderChildren` | `parent_id, new_order: tuple[NodeId, ...]` | Reorder children |

All operations share: `parent_ops: tuple[OperationId, ...]` (DAG parents), `timestamp`,
`principal_id: str | None` (who performed this operation — populated by security layer,
`None` for pre-security or SYSTEM operations). Using `str` instead of `PrincipalId` avoids
a core → security layer dependency.

**Union type:** `type Operation = CreateNode | UpdateNode | DeleteNode | ...`

**Functions:** `operation_to_dict`, `operation_from_dict`, `compute_operation_id`

**Design:** `UpdateNode` carries full state, not diffs. Simpler to implement/replay;
diffs can be computed later. `parent_ops` references form the Merkle DAG — genesis
operations have `parent_ops = ()`.

**Tests:** `tests/uaf/core/test_operations.py` (~12 tests)

---

### Phase 6: Core Exports — `src/uaf/core/__init__.py`

Wire up `__all__` with all public types and functions from phases 1-5.

---

### Phase 7: Operation Log — `src/uaf/db/operation_log.py`

**LogEntry** — frozen dataclass: `operation_id: OperationId, operation: Operation`

**OperationLog** — append-only, content-addressed log:

| Method | Description |
|--------|-------------|
| `append(op) -> OperationId` | Validates parent refs, computes hash, stores |
| `get(op_id) -> LogEntry` | O(1) lookup by content hash |
| `head_ids -> frozenset[OperationId]` | DAG leaves (ops not referenced as parents) |
| `entries_since(op_id) -> list` | All entries after a given op (for sync) |
| `ancestors(op_id) -> list` | Walk DAG backwards to genesis |
| `__len__`, `__iter__` | Length and ordered iteration |

**Internals:** `_entries: list[LogEntry]` (ordered) + `_index: dict[OperationId, LogEntry]` (O(1) lookup)

**Tests:** `tests/uaf/db/test_operation_log.py` (~12 tests: append chains, DAG forking,
parent validation, head tracking, idempotent append)

---

### Phase 8: State Materializer — `src/uaf/db/materializer.py`

**MaterializedState** — mutable dataclass:
```
nodes: dict[NodeId, NodeData]
edges: dict[EdgeId, Edge]
children_order: dict[NodeId, list[NodeId]]
node_last_op: dict[NodeId, OperationId]
```

**StateMaterializer** — replays operations into state:

| Method | Description |
|--------|-------------|
| `apply(entry)` | Dispatch via `match` to per-operation handlers |
| `replay(log)` | Full replay from genesis (clear + apply all) |
| `get_node(id)`, `get_edge(id)` | Direct state lookups |
| `get_children(parent_id)` | Ordered child IDs |

**Design:** On `CreateEdge` with type `CONTAINS`, updates `children_order`. On `DeleteNode`,
children are NOT cascade-deleted (preserves transclusion semantics — nodes can have
multiple parents). Orphan garbage collection is a future concern.

**Tests:** `tests/uaf/db/test_materializer.py` (~14 tests: each op type, replay idempotency,
no-cascade-delete)

---

### Phase 9: EAVT Indexes — `src/uaf/db/eavt.py`

**Datom** — frozen dataclass with `order=True`:
```
entity: str, attribute: str, value: str, tx: str
```
(All strings for total ordering. Original types recoverable via schema.)

Four index orderings as separate dataclasses: `AEVTDatom`, `AVETDatom`, `VAETDatom`

**EAVTIndex** — maintains 4 `SortedList` instances:

| Method | Index Used | Use Case |
|--------|-----------|----------|
| `entity_attrs(e)` | EAVT | "All attributes of node X" |
| `entity_attr(e, a)` | EAVT | "Attribute Y of node X" |
| `attr_entities(a)` | AEVT | "All nodes with attribute Y" |
| `attr_value(a, v)` | AVET | "Nodes where attr=val" |
| `value_refs(v)` | VAET | "All nodes referencing X" |
| `add(datom)` | All 4 | Insert into all indexes |
| `retract_entity(e)` | All 4 | Remove all datoms for entity |

**Tests:** `tests/uaf/db/test_eavt.py` (~12 tests: all query patterns, prefix scans,
scale test with 1000 datoms)

---

### Phase 10: Query Engine — `src/uaf/db/query.py`

**QueryEngine(state, index)** — read-only high-level API:

| Method | Source | Returns |
|--------|--------|---------|
| `get_node(id)` | State dict | `NodeData \| None` |
| `get_children(parent_id)` | State children_order | `list[NodeData]` |
| `get_parent(node_id)` | State edges | `NodeData \| None` |
| `get_references_to(target_id)` | VAET index | `list[NodeData]` |
| `find_by_type(node_type)` | AVET index | `list[NodeData]` |
| `find_by_attribute(attr, val)` | AVET index | `list[NodeData]` |
| `get_edges_from(source_id)` | State edges | `list[Edge]` |
| `count_nodes()`, `count_edges()` | State dicts | `int` |

**Tests:** `tests/uaf/db/test_query.py` (~14 tests using pre-populated graph fixture)

---

### Phase 11: GraphDB Facade — `src/uaf/db/graph_db.py`

**GraphDB** — the main entry point:

**Mutation:** `apply(op) -> OperationId` orchestrates log → materialize → index.
Convenience methods: `create_node`, `update_node`, `delete_node`, `create_edge`, `delete_edge`.

**Query:** Delegates to QueryEngine.

**History:** `get_history(node_id) -> list[LogEntry]`

**Tree traversal:**
- `descendants(node_id) -> set[NodeId]` — recursively walk CONTAINS edges to get all
  nodes within a subtree. Used by Lenses for rendering, by security layer for
  per-artifact permission scoping (see `004-application-layer.md` §8).

**Blob storage:**
- `store_blob(data: bytes) -> BlobId` — hash and store binary data
- `get_blob(blob_id: BlobId) -> bytes | None` — retrieve by content hash

**Internal:** `_node_to_datoms(node, op_id) -> list[Datom]` extracts datoms from a node's
typed fields for EAVT indexing. On update, deindexes old datoms and indexes new ones.

**Tests:** `tests/uaf/db/test_graph_db.py` (~15 end-to-end tests)

---

### Phase 12: DB Exports + Integration Tests

**`src/uaf/db/__init__.py`** — wire up `__all__`

**`tests/uaf/db/test_integration.py`** — scenario tests:
1. **Document authoring:** Create artifact → add heading + paragraphs → reorder → query tree
2. **Spreadsheet:** Create sheet artifact → add cells + formulas → query by type
3. **Transclusion:** Shared paragraph referenced from two artifacts
4. **History:** Create → update 5x → verify full operation history
5. **Orphan semantics:** Delete parent → children remain (no cascade)
6. **Ownership:** Create nodes with owners → query by owner via AVET index

---

### Phase 13: Round-Trip Format Fidelity Tests

**Goal:** Prove the format works end-to-end by importing real files, converting to UAF
nodes/edges, exporting back to the original format, and verifying the output is
*essentially identical* to the input.

**Architecture:**

```
┌──────────┐     import()      ┌──────────┐     export()      ┌──────────┐
│ Original │  ──────────────►  │   UAF    │  ──────────────►  │ Rebuilt  │
│   File   │                   │  Graph   │                   │   File   │
└──────────┘                   └──────────┘                   └──────────┘
      │                                                             │
      └──────────────── compare() ──────────────────────────────────┘
                    "essentially the same"
```

**Importer/Exporter interface** (`src/uaf/app/formats/`):

```python
class FormatHandler(Protocol):
    """Protocol for import/export of a file format."""
    def import_file(self, path: Path, db: GraphDB) -> NodeId:
        """Import a file into the graph. Returns the root Artifact ID."""
        ...
    def export_file(self, db: GraphDB, root_id: NodeId, path: Path) -> None:
        """Export an artifact from the graph to a file."""
        ...

class FormatComparator(Protocol):
    """Protocol for comparing original vs. rebuilt files."""
    def compare(self, original: Path, rebuilt: Path) -> ComparisonResult:
        """Compare two files, ignoring allowed differences."""
        ...
```

**What "essentially the same" means — per format:**

| Format | Must Match | Allowed to Differ |
|--------|-----------|-------------------|
| `.md` | All text content, heading levels, code blocks, link targets, list structure | Trailing whitespace, blank line count, heading style (`#` vs `===`) |
| `.csv` | All cell values, column count, row count | Quoting style (quoted vs unquoted), trailing newline |
| `.txt` | All text content, line breaks | Trailing whitespace |
| `.docx` | Paragraph text, heading levels, bold/italic/underline, table structure, image content (by hash) | Author, creation date, revision number, internal XML IDs, default styles |
| `.xlsx` | Cell values, cell types (number/text/formula), formula expressions, sheet names, sheet count | Author, creation date, calc chain order, default styles, column widths |
| `.pptx` | Slide count, text content per slide, shape count, image content (by hash) | Author, creation date, slide layout IDs, exact coordinates (within tolerance) |
| `.json` | All keys and values, nesting structure | Key ordering, whitespace formatting |
| `.html` | Text content, heading levels, link targets, image sources, table structure | Attribute ordering, whitespace, doctype, meta tags |
| `.pgn` | Move sequence, result, player names, annotations | Tag ordering, move number formatting, comment whitespace |

**`ComparisonResult` dataclass:**

```python
@dataclass
class ComparisonResult:
    is_equivalent: bool
    differences: list[str]     # human-readable list of differences found
    ignored: list[str]         # differences that were intentionally ignored
    similarity_score: float    # 0.0-1.0, where 1.0 = identical content
```

**V1 round-trip implementations** (start with simplest, highest-value formats):

| Priority | Format | Import Lib | Export Lib | Difficulty |
|----------|--------|-----------|-----------|------------|
| 1 | Markdown (`.md`) | `mistune` | Custom renderer | Low |
| 2 | CSV (`.csv`) | `csv` stdlib | `csv` stdlib | Low |
| 3 | Plain text (`.txt`) | Line split | Line join | Trivial |
| 4 | Word (`.docx`) | `python-docx` | `python-docx` | Medium |
| 5 | Excel (`.xlsx`) | `openpyxl` | `openpyxl` | Medium |

**Test fixtures** (`tests/fixtures/`):

Each format gets a set of fixture files that exercise key features:

```
tests/fixtures/
  markdown/
    simple.md           # headings, paragraphs, bold/italic
    with_code.md        # fenced code blocks, inline code
    with_links.md       # links, images
    with_lists.md       # ordered, unordered, nested
    complex.md          # all features combined
  csv/
    simple.csv          # basic rows and columns
    with_types.csv      # numbers, strings, empty cells
    large.csv           # 1000 rows (performance sanity check)
  txt/
    simple.txt
    multiline.txt
  docx/
    simple.docx         # paragraphs and headings
    formatted.docx      # bold, italic, underline, fonts
    with_tables.docx    # tables with data
    with_images.docx    # embedded images
  xlsx/
    simple.xlsx         # values only
    with_formulas.xlsx  # SUM, VLOOKUP, etc.
    multi_sheet.xlsx    # multiple sheets
```

**Test structure** (`tests/uaf/app/test_roundtrip.py`):

```python
@pytest.mark.parametrize("fixture", markdown_fixtures)
def test_markdown_roundtrip(fixture: Path, tmp_path: Path) -> None:
    """Import markdown → UAF graph → export markdown → compare."""
    db = GraphDB()
    root_id = MarkdownHandler().import_file(fixture, db)
    output = tmp_path / fixture.name
    MarkdownHandler().export_file(db, root_id, output)
    result = MarkdownComparator().compare(fixture, output)
    assert result.is_equivalent, f"Differences: {result.differences}"
    assert result.similarity_score >= 0.95

# Same pattern for csv, txt, docx, xlsx
```

**Source files:**

| File | Purpose |
|------|---------|
| `src/uaf/app/formats/__init__.py` | FormatHandler/FormatComparator protocols |
| `src/uaf/app/formats/markdown.py` | Markdown import/export/compare |
| `src/uaf/app/formats/csv_format.py` | CSV import/export/compare |
| `src/uaf/app/formats/plaintext.py` | Plain text import/export/compare |
| `src/uaf/app/formats/docx.py` | Word import/export/compare (future) |
| `src/uaf/app/formats/xlsx.py` | Excel import/export/compare (future) |
| `tests/uaf/app/test_roundtrip.py` | Parametrized round-trip tests |
| `tests/fixtures/` | Sample files for each format |

**Dependencies to add for Phase 13:**

```toml
dependencies = [
    "sortedcontainers>=2.4",
    "mistune>=3.0",        # Markdown parsing
]
# docx/xlsx deps added when those handlers are built:
# "python-docx>=1.1", "openpyxl>=3.1"
```

**Tests:** ~25 round-trip tests (5 markdown fixtures + 3 CSV + 2 txt + parametrized)

**Verification:** All round-trip tests pass with `similarity_score >= 0.95`. Any
content difference causes a test failure with a human-readable diff showing exactly
what didn't survive the round-trip.

---

## 5. File Summary

### Source Files (14 core/db + 4 format handlers)

| File | Purpose |
|------|---------|
| `src/uaf/core/node_id.py` | NodeId, EdgeId, OperationId, BlobId, utc_now |
| `src/uaf/core/errors.py` | UAFError hierarchy (shared by db + security layers) |
| `src/uaf/core/nodes.py` | Node types, NodeData union, NodeMetadata |
| `src/uaf/core/edges.py` | Edge, EdgeType |
| `src/uaf/core/serialization.py` | Deterministic serialization + content hashing |
| `src/uaf/core/operations.py` | Operation types + Operation union |
| `src/uaf/core/__init__.py` | Public exports |
| `src/uaf/db/operation_log.py` | Append-only content-addressed log |
| `src/uaf/db/materializer.py` | Replay operations → current state |
| `src/uaf/db/eavt.py` | 4 covering indexes (EAVT/AEVT/AVET/VAET) |
| `src/uaf/db/query.py` | High-level query API |
| `src/uaf/db/graph_db.py` | Facade composing all components |
| `src/uaf/db/__init__.py` | Public exports |
| `src/uaf/app/formats/__init__.py` | FormatHandler / FormatComparator protocols |
| `src/uaf/app/formats/markdown.py` | Markdown import/export/compare |
| `src/uaf/app/formats/csv_format.py` | CSV import/export/compare |
| `src/uaf/app/formats/plaintext.py` | Plain text import/export/compare |

### Test Files (12 + fixtures)

```
tests/uaf/core/test_node_id.py        (~12 tests)
tests/uaf/core/test_nodes.py          (~20 tests)
tests/uaf/core/test_edges.py          (~8 tests)
tests/uaf/core/test_serialization.py  (~18 tests)
tests/uaf/core/test_operations.py     (~12 tests)
tests/uaf/db/test_operation_log.py    (~12 tests)
tests/uaf/db/test_materializer.py     (~14 tests)
tests/uaf/db/test_eavt.py             (~12 tests)
tests/uaf/db/test_query.py            (~14 tests)
tests/uaf/db/test_graph_db.py         (~15 tests)
tests/uaf/db/test_integration.py      (~6 scenario tests)
tests/uaf/app/test_roundtrip.py       (~25 round-trip tests)
tests/fixtures/                        (sample files per format)
```

**Total: ~168 tests**

---

## 6. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Frozen dataclasses with `slots=True` | Immutable, memory-efficient value objects |
| Composition over inheritance | Frozen dataclasses don't support inheritance; `meta: NodeMetadata` is cleaner |
| `UpdateNode` carries full state | Simpler to replay than diffs; diffs computable later |
| Datom values are strings | Total ordering for SortedList; type recovery via schema |
| No delete cascade | Preserves transclusion (node can have multiple parents) |
| No persistence in V1 | In-memory only; OperationLog interface makes persistence a future swap-in |
| No thread safety in V1 | Single-writer assumption; concurrent multi-user editing requires CRDT sync (Appendix B), not thread locks |
| Optional `owner` on NodeMetadata | Simple ownership; richer models via OWNED_BY edges |
| "Artifact" not "Document" | Top-level container is format-agnostic |

### Functional Programming Principles

The architecture leans functional where it reduces bugs and increases maintainability,
without being dogmatic about it.

**Immutable data everywhere except one explicit boundary.**
All nodes, edges, operations, datoms, and log entries are frozen dataclasses (`frozen=True`,
`slots=True`). You cannot accidentally mutate a node after creation — the runtime prevents
it. The *only* mutable structure is `MaterializedState`, and it lives behind a single
entry point (`GraphDB.apply()`). This makes the mutable boundary explicit and auditable.

**Operations as data (event sourcing = fold/reduce).**
The entire database is a `functools.reduce` over an operation log:
```
current_state = reduce(apply_operation, operation_log, empty_state)
```
This is inherently functional — the operation log is an immutable append-only sequence,
and `apply_operation` is a pure function from `(state, op) → state`. History, undo, and
branching fall out naturally.

**Pure functions for core logic.**
Serialization (`node_to_dict`, `canonical_json`), hashing (`content_hash`), datom
extraction (`_node_to_datoms`), and all query methods are pure functions — same input,
same output, no side effects. This makes them trivially testable and safe to call from
anywhere.

**Algebraic data types via `match` exhaustiveness.**
`NodeData` and `Operation` are union types (`type NodeData = Artifact | Paragraph | ...`).
Pattern matching with `match` gives exhaustiveness checking — if you add a new node type
and forget to handle it somewhere, mypy catches it at compile time. This replaces the
visitor pattern and class hierarchies common in OOP.

**Structural typing (protocols) over nominal typing (inheritance).**
`FormatHandler` and `FormatComparator` are `Protocol` classes — any object with the right
method signatures satisfies the interface. No base class registration, no `super().__init__()`.
This is how Go interfaces and Haskell type classes work. It keeps classes decoupled and
makes testing with fakes trivial.

**Prefer returning new values over mutation.**
`UpdateNode` creates a new node with updated fields; it doesn't mutate the existing one.
Serialization returns new dicts; it doesn't modify inputs. When you see `->` in a function
signature, you know it produces a new value.

**Where we're NOT functional (pragmatism).**
`MaterializedState` uses mutable dicts for performance — rebuilding a 100K-node immutable
dict on every operation would be prohibitively slow. `SortedList` is mutable. The key
discipline is: mutation is confined to `StateMaterializer.apply()` and `EAVTIndex.add()/retract()`,
never scattered across the codebase. Think of it as the "IO monad boundary" — pure
functions compute what should change, and a thin mutable layer executes it.

---

## 7. Verification

### Per-phase gate
```bash
make check   # ruff check + mypy strict + pytest — must pass after every phase
```

### Four levels of testing

**Level 1 — Unit tests (Phases 1-6):** Node/edge models, serialization round-trips,
content hashing determinism. No database. Pure functions in, values out.

**Level 2 — Integration tests (Phases 7-12):** OperationLog → StateMaterializer →
EAVTIndex → QueryEngine → GraphDB. All in-memory, no external services. Scenario
tests (document authoring, spreadsheet, transclusion, history).

**Level 3 — Round-trip fidelity tests (Phase 13):** Real files in → UAF graph →
real files out. For each format:
1. Import a fixture file into GraphDB
2. Export the graph back to the same format
3. Compare original vs. rebuilt using format-specific comparators
4. Assert `similarity_score >= 0.95` and `is_equivalent == True`
5. Any content loss causes a test failure with a readable diff

Allowed to differ: author metadata, creation dates, internal IDs, formatting
defaults, whitespace normalization. Must match: all content, structure, formulas,
images (by content hash), links, and semantic elements.

**Level 4 — Performance benchmarks (future, nightly CI):**
Insert 10K/100K/1M nodes, measure query latency. Regression thresholds.

### After all phases
```bash
make check   # ~168 tests passing, zero mypy errors, zero ruff violations
```

---

## Appendix A: File Format Compatibility

The UAF graph architecture can ingest and export a wide range of existing file formats.
Each format maps to specific node types and edge patterns.

### Text / Document Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| Microsoft Word | `.docx` | `python-docx` parser → paragraphs, headings, tables, images | Artifact, Paragraph, Heading, TextBlock, Image |
| Google Docs | `.gdoc` (JSON) | Google Docs API export → same as docx | Same as docx |
| OpenDocument Text | `.odt` | `odfpy` parser | Same as docx |
| Markdown | `.md` | `markdown-it` or `mistune` parser → AST → nodes | Artifact, Paragraph, Heading, TextBlock, CodeBlock, Image |
| HTML | `.html` | `beautifulsoup4` → DOM tree → node tree | Artifact, Paragraph, Heading, TextBlock, Image, Shape |
| PDF | `.pdf` | `pdfplumber` or `unstructured` → text blocks + LayoutHint metadata | Artifact, TextBlock, Image (layout preserved via LayoutHint; semantic structure inferred via heuristics) |
| Rich Text Format | `.rtf` | `striprtf` or `pyrtf-ng` | Artifact, Paragraph, TextBlock |
| Plain Text | `.txt` | Line-split → paragraphs | Artifact, Paragraph |
| LaTeX | `.tex` | `pylatexenc` or `pandoc` | Artifact, Paragraph, Heading, FormulaCell, CodeBlock |
| EPUB | `.epub` | `ebooklib` → XHTML chapters → node tree | Artifact, Paragraph, Heading, Image |

### Spreadsheet / Data Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| Microsoft Excel | `.xlsx` | `openpyxl` → cells, formulas, charts | Artifact, Sheet, Cell, FormulaCell |
| Google Sheets | `.gsheet` | Google Sheets API export | Same as xlsx |
| OpenDocument Spreadsheet | `.ods` | `odfpy` or `openpyxl` | Same as xlsx |
| CSV | `.csv` | `csv` stdlib → cells | Artifact, Sheet, Cell |
| TSV | `.tsv` | Same as CSV | Same as CSV |
| JSON | `.json` | `json` stdlib → key-value nodes or table rows | Artifact, Cell (flat) or nested nodes |
| Parquet | `.parquet` | `pyarrow` → columnar → cells | Artifact, Sheet, Cell |
| SQLite | `.sqlite` | `sqlite3` stdlib → tables → sheets, rows → cells | Artifact, Sheet, Cell |

### Presentation Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| Microsoft PowerPoint | `.pptx` | `python-pptx` → slides, text boxes, shapes | Artifact, Slide, TextBlock, Shape, Image |
| Google Slides | `.gslides` | Google Slides API export | Same as pptx |
| OpenDocument Presentation | `.odp` | `python-pptx` / `odfpy` | Same as pptx |

### Code Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| Python | `.py` | `ast` stdlib → AST nodes | Artifact, CodeBlock + AST sub-nodes |
| JavaScript/TypeScript | `.js`/`.ts` | `tree-sitter` → AST | Same |
| Any language | `.*` | `tree-sitter` (universal) | Artifact, CodeBlock |
| Jupyter Notebook | `.ipynb` | `json` → code cells + markdown cells + outputs | Artifact, CodeBlock, TextBlock, Image |

### Project Management Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| Jira Export | `.json`/`.csv` | Jira API or CSV export → issues | Artifact, Task |
| Microsoft Project | `.mpp` | `mpxj` (Java, via subprocess) | Artifact, Task (with DEPENDS_ON edges) |
| iCalendar | `.ics` | `icalendar` library | Artifact, Task (events as tasks) |
| Todoist Export | `.json`/`.csv` | JSON/CSV parser | Artifact, Task |

### Diagram / Visual Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| SVG | `.svg` | `xml.etree` → shape elements | Artifact, Shape, TextBlock |
| Visio | `.vsdx` | `python-pptx` (shared OOXML) or specialized lib | Artifact, Shape (with LINKED_TO edges) |
| Mermaid | `.mmd` | Text parser → graph structure | Artifact, Shape (with edges) |
| Draw.io/diagrams.net | `.drawio` | XML parser → shapes + connectors | Artifact, Shape |

### Music Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| MusicXML | `.mxl`/`.musicxml` | `music21` library → notes, measures | Artifact + custom music nodes (future) |
| MIDI | `.mid` | `mido` or `music21` → note events | Same |
| MuseScore | `.mscz` | `music21` import | Same |
| LilyPond | `.ly` | Text parser or `music21` | Same |

### Chess / Game Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| PGN (Portable Game Notation) | `.pgn` | `chess` library (`python-chess`) → move tree | Artifact + custom MoveNode (future) |
| FEN (board state) | inline | `python-chess` → board position | Single node snapshot |
| SGF (Go) | `.sgf` | `sgfmill` → move tree | Same pattern as PGN |

### CAD / 3D Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| STEP | `.step`/`.stp` | `cadquery` or `OCP` → B-rep operations | Artifact + custom CAD nodes (future) |
| STL | `.stl` | `numpy-stl` → mesh data | Artifact + mesh node |
| IGES | `.iges` | `OCP` (OpenCascade) | Same as STEP |
| FreeCAD | `.FCStd` | FreeCAD Python API | Artifact + CAD operation nodes |

### Data Exchange / Structured Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| XML | `.xml` | `xml.etree` → tree of nodes | Artifact + nested nodes |
| YAML | `.yaml` | `pyyaml` → dict tree → nodes | Same |
| TOML | `.toml` | `tomllib` (stdlib 3.11+) → dict → nodes | Same |
| Protocol Buffers | `.proto` | Schema → type definitions | Schema nodes |
| Avro | `.avro` | `fastavro` → records → cells | Artifact, Sheet, Cell |

### Archive / Compound Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| ZIP (containing above) | `.zip` | `zipfile` → extract → process each file | Multiple Artifacts |
| Email (MIME) | `.eml` | `email` stdlib → headers + body + attachments | Artifact, TextBlock, Image |
| MBOX | `.mbox` | `mailbox` stdlib → individual emails | Multiple Artifacts |

### Geographic / GIS Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| GeoJSON | `.geojson` | `json` stdlib → Feature/Geometry tree | Artifact + custom Feature, Geometry nodes (future) |
| KML | `.kml` | `xml.etree` → placemarks, paths | Same |
| Shapefile | `.shp` | `fiona` or `pyshp` → features + attributes | Same |
| GeoPackage | `.gpkg` | `fiona` → SQLite-based features | Same |

### Scientific / Research Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| HDF5 | `.h5`/`.hdf5` | `h5py` → groups + datasets | Artifact + custom Tensor/Matrix nodes (future) |
| NetCDF | `.nc` | `netCDF4` or `xarray` → dimensions + variables | Same |
| FITS (astronomy) | `.fits` | `astropy.io.fits` → headers + image data | Artifact + Image + metadata nodes |

### Medical / Health Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| DICOM | `.dcm` | `pydicom` → patient/study/series/image hierarchy | Artifact + custom medical nodes (future) |
| HL7 FHIR | `.json` | FHIR JSON → resource bundles | Artifact + nested record nodes |

### Legal / Regulatory Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| Akoma Ntoso | `.xml` | `xml.etree` → legislative document structure | Artifact, Paragraph, Heading + custom legal nodes (future) |
| XBRL (financial reporting) | `.xbrl`/`.xml` | `arelle` → facts + contexts | Artifact + custom financial nodes (future) |

### Video / Audio Metadata Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| SRT (subtitles) | `.srt` | Text parser → timed text entries | Artifact + custom TimedText nodes (future) |
| WebVTT (subtitles) | `.vtt` | Text parser → timed cues with styling | Same |
| Podcast chapters | `.json`/`.xml` | JSON/XML → chapter markers | Artifact + custom Chapter nodes (future) |

### API / Schema Specification Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| OpenAPI / Swagger | `.yaml`/`.json` | `pyyaml`/`json` → endpoints + schemas | Artifact + nested schema nodes |
| GraphQL SDL | `.graphql` | `graphql-core` → type definitions | Artifact + schema nodes |
| JSON Schema | `.json` | `json` → schema tree | Artifact + nested schema nodes |

### Contact / Personal Information Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| vCard | `.vcf` | `vobject` → contact fields | Artifact + field nodes |
| Outlook Message | `.msg` | `extract-msg` → headers + body + attachments | Artifact, TextBlock, Image |

### Finance / Accounting Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| QIF (Quicken) | `.qif` | Text parser → transactions | Artifact, Sheet, Cell |
| OFX (Open Financial Exchange) | `.ofx` | `ofxparse` → accounts + transactions | Same |

### Desktop Publishing Formats

| Format | Extension | Import Strategy | Node Types Used |
|--------|-----------|----------------|-----------------|
| InDesign | `.indd`/`.idml` | IDML (ZIP of XML) → spreads, text frames, images | Artifact, TextBlock, Image, Shape + LayoutHint |
| QuarkXPress | `.qxp` | Limited — may need export to IDML first | Same |

### Key Import/Export Principles

1. **Import preserves layout when available.** PDF → nodes captures layout coordinates,
   fonts, and colors via `LayoutHint` metadata. What's inherently lossy is *semantic
   structure* (PDF doesn't know "this is a heading"), but heuristics infer much of it.
2. **Export reconstructs format.** Nodes → `.docx` uses `python-docx` to build a new
   document from the node tree. Fidelity depends on how much semantic information
   the original import captured.
3. **Round-trip fidelity** is highest for structured formats (docx, xlsx, pptx) and
   lowest for visual formats (PDF, images).
4. **New formats** require only a parser (import) and a builder (export). The core
   node/edge model does not change.
5. **Chess example:** A PGN file becomes an Artifact with child MoveNodes connected by
   FOLLOWS edges. Variations are branches (multiple FOLLOWS edges from one position).
   Annotations are TextBlock children of MoveNodes. The ChessLens renders this as a
   move list + board visualization.

---

## Appendix B: Future Phases (Not in V1)

| Phase | Description | Depends On |
|-------|-------------|------------|
| MCP Server | Expose GraphDB as MCP tools for AI agents | Phase 11 (GraphDB API) |
| Persistence | SQLite-backed OperationLog + snapshot/restore | Phase 7 (OperationLog interface) |
| CRDT Sync | Eg-walker merge for concurrent branches — **required** for multi-user editing | Phase 7 (DAG structure) |
| Fractional Indexing | `fractional-indexing` lib for concurrent reordering | Phase 8 (children_order) |
| JSON-LD Export | Serialize graph to W3C JSON-LD format | Phase 4 (serialization) |
| Type-Specialized Storage | DuckDB for sheets, rope for text | Phase 9 (EAVT as routing layer) |
| Ghost Ingestion Pipeline | Import parsers for all Appendix A formats | Phase 11 (GraphDB API) |
| Access Control | Node/edge-level permissions via security layer — see `003-security-layer.md` | Phase 11 + security layer (Phases S1-S6) |
| Vector Embeddings | AI/Graph-RAG metadata on nodes | Phase 2 (NodeMetadata extension) |
| Rust Core (PyO3) | Rewrite hot paths (hashing, indexes, CRDT) in Rust — see below | Profiling data from V1 |
| Native Rust Database | Purpose-built database engine with wire protocol — see below | Rust Core + Persistence |

### Path to a Native Rust Database

The architecture is deliberately designed so that every component maps to well-understood
database primitives that Rust excels at:

| UAF Component | Database Primitive | Rust Ecosystem |
|---------------|-------------------|----------------|
| Operation log (append-only) | Write-ahead log (WAL) | `sled`, custom B-tree |
| EAVT indexes (SortedList) | Covering B-tree indexes | `sled`, `rocksdb`, `redb` |
| State materializer | Materialized view / event projection | Standard pattern |
| Content hashing (SHA-256) | Merkle tree (git's object store) | `sha2` crate, zero-copy |
| CRDT merge (eg-walker) | Conflict-free replication | `automerge-rs` (already Rust) |
| Blob store | Content-addressed object store | Filesystem or S3, trivial in Rust |

**Migration path (incremental, not a rewrite):**

1. **V1:** Pure Python — current plan. Correct, well-tested, well-typed.
2. **V2 (PyO3 hot paths):** Replace `content_hash`, `EAVTIndex.add/retract`, and CRDT
   merge with Rust modules exposed via PyO3. Same Python API, 10-50x speedup on hot
   paths. This is exactly how Polars, DuckDB, Loro, and Automerge work.
3. **V3 (Rust database engine):** Full Rust implementation of OperationLog + EAVT +
   Materializer + QueryEngine. Exposes a wire protocol (gRPC or custom). The Python
   app layer becomes a thin client — same `GraphDB` interface, backed by a Rust server.
4. **V4 (Language-agnostic clients):** With a wire protocol, any language can be a client:
   JS/WASM (browser), Swift (iOS/macOS), Kotlin (Android), Go (infrastructure). The
   database becomes a standalone product.

**Why this works without rearchitecting:** The clean interfaces between components
(OperationLog, StateMaterializer, EAVTIndex, QueryEngine, GraphDB) are exactly the
module boundaries where Rust rewrites slot in. Each component is independently testable
and has a clear contract. Replacing one component's internals doesn't affect the others.

**When to start:** After V1 is working and profiled. Rewriting before we have usage
data means optimizing the wrong things. The Python V1 tells us *what* is slow; Rust
fixes *how* to make it fast.

---

## Appendix C: Open Design Questions

These questions are real but cannot be fully resolved until later layers exist or real
usage patterns emerge. Current thinking and options are documented here so we don't lose
context.

### C1. Undo / Redo Semantics

**The problem:** The event-sourced architecture makes undo *mechanically possible* (we
have the full operation history), but "undo" is semantically ambiguous. If a user does
`create paragraph → create heading → delete paragraph`, does "undo" reverse the delete
(restoring the paragraph) or reverse the last three operations?

**Options:**

| Approach | How It Works | Pros | Cons |
|----------|-------------|------|------|
| **Operation-level undo** | Append an "inverse operation" (e.g., `DeleteNode` → `CreateNode` with same data) | Simple, composable, preserves history | Doesn't handle compound actions ("undo paste of 50 nodes") |
| **Semantic undo (command grouping)** | Group related operations into a "command" (e.g., paste = 50 CreateNodes). Undo reverses the whole group | Matches user intent | Requires the application layer to define command boundaries |
| **Branch-based undo** | Fork the DAG at the undo point, creating an alternative branch | Clean history, supports redo as branch switching | Complex to implement, unfamiliar UX |

**Current leaning:** Semantic undo with command grouping. The application layer (Lenses)
groups operations into user-visible commands. The database layer provides `undo_command(group_id)`
which appends inverse operations for everything in the group. This keeps the db layer
simple (it just appends inverses) while the app layer defines what "one action" means.

**V1 impact:** None. Undo is an application-layer concern. The database layer already
stores enough information (full history, operation ordering) to support any approach.

### C2. Crash Safety / Demo Persistence

**The problem:** V1 is purely in-memory. If the process dies during the VC demo,
everything is lost.

**Options:**

| Approach | Effort | Reliability |
|----------|--------|-------------|
| **JSON snapshot on explicit save** | Low — `json.dumps(operation_log)` to file | Manual, user must remember to save |
| **Append-only journal file** | Medium — write each operation to a JSONL file as it's appended | Automatic, survives crash (last op may be partial) |
| **SQLite WAL** | Medium-high — full persistence layer | Production-grade, but pulls forward Phase "Persistence" |

**Current leaning:** Append-only JSONL journal. On startup, replay the journal to rebuild
state. On each `apply()`, append the operation as one JSON line. This is ~10 lines of code
on top of the existing `OperationLog` and gives crash safety without a full persistence
layer.

**V1 impact:** Optional. Can be added as a `JournaledGraphDB` wrapper around `GraphDB`
without changing any interfaces. Good candidate for a quick addition after Phase 12 if
demo reliability is a concern.

### C3. Transclusion + Permissions Interaction — *Resolved in `003-security-layer.md`*

**The problem:** If node X is transcluded into artifacts A and B, and user 1 owns A but
not B, can user 1 edit X? Can user 1 even *see* X when viewing artifact B?

**Resolution:** Artifact-level ACLs with "inherit from parent" default. Transclusion
does NOT grant access — viewing a transcluded node requires READ permission on both the
source and destination artifacts. Node-level permission overrides handle exceptions.
See `003-security-layer.md` §2 "Transclusion and Permissions" for full details.

**V1 impact:** None. V1 has no security layer. The `owner` field on `NodeMetadata` is
informational only.

### C4. Memory Growth / Garbage Collection

**The problem:** The operation log is append-only — it never shrinks. Deleted nodes stay
in history. Orphaned nodes (no parent after a delete) remain in `MaterializedState`.
Over time, memory grows without bound.

**Scale of the problem:**
- A 10K-node artifact with 100 edits/day generates ~36K operations/year
- At ~500 bytes/operation, that's ~18 MB/year of operation log — manageable
- The real concern is `MaterializedState` holding deleted/orphaned nodes

**Options:**

| Approach | How It Works | Tradeoff |
|----------|-------------|----------|
| **Log compaction** | Periodically create a "snapshot" operation that represents current state. Discard operations before the snapshot | Bounds log size, but loses fine-grained history before the snapshot |
| **Orphan GC** | Periodically walk the containment tree from all artifact roots. Nodes not reachable from any root are garbage | Reclaims memory, but breaks if a node is temporarily orphaned (e.g., mid-move) |
| **Tombstone expiry** | Deleted nodes are kept for N days (for undo), then permanently removed | Predictable memory, but permanent data loss after expiry |
| **Tiered storage** | Hot operations in memory, cold operations on disk. LRU eviction | Bounded memory, preserves all history, but requires persistence layer |

**Current leaning:** Tiered storage is the right long-term answer (it's essentially the
persistence layer from Appendix B). For V1, memory growth is not a concern — a demo with
10K nodes and a few hundred operations fits comfortably in memory. If we add the JSONL
journal (C2), we get "persistent but not compacted," which is fine for months of demo use.

**V1 impact:** None. Add a `node_count` / `operation_count` method to GraphDB so we can
monitor growth. Actual GC is a post-V1 concern.

---

## Appendix D: UAF as AI Training Data — Structural Advantages Over Flat Text

> **Note:** This appendix explores how the UAF graph structure could improve AI training
> beyond current approaches. This is a long-term strategic opportunity, not a V1 concern,
> but the data model decisions in V1 directly enable it.

### The Problem with Current Training Data

Current LLM training feeds models flat text — tokenized sequences where all structure,
relationships, and provenance are destroyed. The model learns statistical co-occurrence
patterns and must *reconstruct* structure from context. This is the root cause of
hallucination: the model is pattern-matching on surface text, not reasoning over explicit
relationships.

```
Current pipeline:
  Structured knowledge → flatten to text → tokenize → train on next-token prediction
  (structure destroyed)           (relationships lost)    (statistical patterns only)

UAF pipeline (potential):
  Structured knowledge → graph with typed nodes + edges → train on graph objectives
  (structure preserved)   (relationships explicit)         (relational reasoning)
```

### What UAF Data Provides That Flat Text Does Not

**1. Typed semantic relationships.**
Instead of learning that "revenue" and "Q1 report" co-occur in text, a model trained on
UAF data sees `Revenue_Cell --CONTAINED_IN--> Q1_Sheet --CONTAINED_IN--> Q1_Report`.
The relationship is typed (`CONTAINS`), directional, and unambiguous. This is the
difference between correlation (co-occurrence) and causation (explicit links).

**2. Edit history as a learning signal.**
The operation log captures *how artifacts evolve* — every creation, edit, deletion, and
reordering. A model trained on edit sequences learns revision patterns: how humans improve
writing, fix code bugs, restructure arguments, iterate on spreadsheet formulas. This is a
strictly richer signal than static snapshots. Current training data is overwhelmingly
point-in-time; UAF data is inherently temporal.

**3. Multi-modal unified representation.**
UAF stores text, code, spreadsheets, images, music, and CAD models in the same typed
graph. A single artifact can contain `Paragraph` → `Image` → `FormulaCell` → `CodeBlock`
nodes, all connected by typed edges. Training on this unified representation could improve
cross-modal reasoning — the model sees that a chart is `LINKED_TO` a data table which
`DEPENDS_ON` a formula, as explicit graph structure rather than adjacent tokens.

**4. Provenance and attribution.**
Every node has `owner`, `created_at`, `updated_at`, and a full operation history linking
it to the principal who created/modified it. Training data with provenance enables models
that can cite sources, track confidence by source reliability, and avoid hallucination by
distinguishing "this fact comes from node X in artifact Y" from "this is a statistical
pattern across the training set."

**5. Transclusion as a knowledge graph signal.**
When the same node is referenced from multiple artifacts via `REFERENCES` edges, the graph
encodes that these artifacts share a common concept. This is explicit cross-document
linking — something that hyperlinks approximate but that UAF makes first-class. A model
trained on transclusion patterns learns how knowledge connects across contexts.

### Novel Training Objectives Enabled by Graph Structure

Beyond next-token prediction, UAF enables training objectives that are closer to reasoning:

| Objective | Description | What It Teaches |
|-----------|-------------|-----------------|
| **Edge prediction** | Given two nodes, predict the edge type between them | Relational reasoning — "what is the relationship between X and Y?" |
| **Node completion** | Given a partial artifact graph, predict the next node type and content | Document structure understanding — "what comes after a heading?" |
| **Graph navigation** | Given a question and a starting node, predict which edges to follow | Retrieval reasoning — "how do I find the answer in this graph?" |
| **Operation prediction** | Given an edit history, predict the next operation | User intent modeling — "what will the user do next?" |
| **Cross-artifact linking** | Given a node, predict which other artifacts it should be transcluded into | Knowledge organization — "where else is this concept relevant?" |
| **Permission-aware retrieval** | Navigate the graph while respecting ACL boundaries | Secure reasoning — "what can I access and what can I not?" |

### How This Improves on Graph-RAG (Vision Document §5)

The vision document describes Graph-RAG as AI agents *navigating* the graph at inference
time. The training data opportunity goes further: instead of just navigating a graph at
inference, **train the model on graph-structured data so it learns to reason relationally.**

```
Graph-RAG (inference-time):
  Model receives question → follows edges in UAF graph → returns answer
  (model's internal knowledge doesn't change; graph is external memory)

Graph-trained model:
  Model trained on UAF graphs → internalizes relational patterns → reasons structurally
  (model learns graph reasoning as a native capability, not just retrieval)
```

The two approaches are complementary: a graph-trained model is better at navigating graphs
at inference time because it has learned relational reasoning patterns during training.

### Data Sovereignty as a Training Advantage

UAF's security layer (ACLs, audit trails, encryption) creates a framework for **consented
training data**:

- **Opt-in training pools:** Artifact owners explicitly grant `TRAINING` permission
  (a future Role) to allow their data to be used for model training
- **Per-artifact granularity:** Users can consent to training on their public documents
  but not their private spreadsheets
- **Audit trail for training data:** The audit log records which artifacts were used for
  training, when, and by whom — full provenance for the training dataset itself
- **Crypto-shredding for training data removal:** If a user revokes consent, destroying
  their encryption key makes their data unrecoverable from the training set (assuming
  encrypted data was used for training, not decrypted copies)
- **GDPR compliance:** This framework satisfies GDPR Article 6 (lawful basis for
  processing) and Article 7 (conditions for consent) for training data

This is a significant commercial advantage in the EU market: organizations can use UAF
for internal AI training with full regulatory compliance and user consent tracking.

### Practical Implementation Path

1. **V1:** Build the graph with typed nodes, edges, and full operation history — this
   is already the plan. No training-specific work needed.
2. **Vector embeddings (Appendix B):** Add embedding metadata to nodes. This enables
   hybrid retrieval (graph navigation + semantic similarity).
3. **Graph export for training:** Export subgraphs as training samples in formats
   consumable by ML frameworks (PyG, DGL, or custom JSONL with graph structure).
4. **Fine-tuning experiments:** Fine-tune an open model (Llama, Mistral) on UAF graph
   data using graph-aware training objectives. Compare against same model fine-tuned
   on the equivalent flat text.
5. **Benchmark:** Measure hallucination rate, citation accuracy, and cross-modal reasoning
   on graph-trained vs. text-trained models using the same underlying knowledge.

### Key Insight

**The data model decisions we make in V1 — typed nodes, typed edges, operation history,
content addressing, transclusion — are exactly what makes the training data valuable.**
We don't need to add anything for AI training; we need to build the graph correctly. The
training value is an emergent property of structured, relational, provenanced data.

---

## Appendix E: Business Continuity, Backup, and Data Recovery

> **Scope:** End-state system (not V1). This appendix addresses the concern that will
> kill adoption faster than any security flaw: "Will I lose my data?" People tolerate
> imperfect security; they do not tolerate data loss or lockout.

### What the Architecture Naturally Provides

Before discussing what we need to *add*, the architecture already has several strong
durability properties by construction:

**1. Local-first means data lives on the user's device.**
The cloud server is a sync relay, not the primary store. If the server goes down — or
the company goes bankrupt — the user still has their complete local copy. This is the
single most important business continuity property. Compare to Google Docs, where a
Google outage means zero access to your documents.

**2. Append-only operation log is naturally durable.**
Data is never overwritten. A "delete" is a new operation appended to the log — the
original creation operation still exists. This means accidental deletion is always
recoverable by replaying the log up to the point before the delete.

**3. CRDT sync creates automatic replicas.**
Every device that syncs an artifact has a full copy of its operation log. If the user
has a laptop and a phone, the data exists in at least three places (laptop, phone,
server). Losing one device doesn't lose data.

**4. Content addressing detects corruption.**
Every operation is SHA-256 hashed. If a disk bit-flips or a storage system silently
corrupts data, the hash check fails on the next read. Corruption is *detected*, not
silently propagated. This is something most systems (including Notion, Google Docs,
and Microsoft 365) do NOT provide.

**5. Format export provides an escape hatch.**
The round-trip format handlers (db plan Phase 13) can export any artifact to standard
file formats (docx, xlsx, csv, markdown). Even if the UAF software itself becomes
unavailable, the user can export their data to formats that every other tool reads.

**6. Open source prevents vendor lock-in.**
The protocol, the schema, and the code are open source. If the company disappears,
anyone can run the software. The data is not trapped in a proprietary format that only
one company's software can read.

### Data Loss Scenarios and Mitigations

#### L1. Device Failure (Disk Crash, Theft, Fire)

**Risk:** User's primary device is destroyed. Local data lost.

**Mitigation:**
- **CRDT sync to cloud** — if sync was active, the server has a full copy of the
  operation log. Data loss is limited to operations since the last sync.
- **Multi-device sync** — if the user has multiple devices, each is an independent
  replica. Device loss is survivable.
- **Server-side backups** — the hosting provider runs automated backups of the
  operation log store (see "Backup Strategy" below).

**Residual risk:** If the user was working offline and hadn't synced, unsynced
operations are lost. Mitigation: configurable auto-sync interval (default: every
30 seconds when online). Clear UI indicator showing "last synced: X minutes ago."

#### L2. Server / Cloud Provider Failure

**Risk:** Cloud hosting goes down (outage, bankruptcy, data center fire).

**Mitigation:**
- **Local-first** — users keep working. No interruption to active use.
- **Multi-region replication** — operation logs replicated across geographically
  separated data centers (standard cloud practice, configured in deployment).
- **Cross-provider backup** — automated daily snapshots to a *different* cloud
  provider (e.g., primary on Hetzner, backup to AWS S3 or Backblaze B2). Encrypted
  before upload.
- **User-initiated export** — users can export their complete graph at any time to
  local files. Encourage periodic "take-home" exports for critical data.

**Residual risk:** If all cloud providers fail simultaneously AND the user's local
device is also gone, data is lost. This is the "asteroid hits both data centers"
scenario — mitigated by geographic distribution but not eliminated.

#### L3. Accidental Deletion

**Risk:** User deletes an artifact or critical nodes by mistake.

**Mitigation:**
- **Append-only log** — "deleted" nodes are not physically removed. The delete
  operation is appended, and the materialized state removes the node from active
  view, but the creation operation and all prior data still exist in the log.
- **Undo** — the application layer's undo mechanism (Appendix C1) reverses delete
  operations by appending inverse operations.
- **Soft-delete with retention period** — deleted artifacts move to a "Trash" state
  for N days (configurable, default: 30 days) before permanent removal from the
  materialized view. Similar to Google Drive's trash.
- **Point-in-time recovery** — because the operation log is ordered and timestamped,
  we can materialize the state at any historical point: "show me my graph as it was
  at 3pm yesterday." This is a natural capability of the event-sourced architecture
  — just stop replaying operations at the desired timestamp.

**Residual risk:** None for data loss. The append-only log means accidental deletion
is always 100% recoverable (unless log compaction has run — see L6).

#### L4. Ransomware / Malicious Deletion

**Risk:** Attacker gains access and deletes or encrypts data.

**Mitigation:**
- **Append-only log with content addressing** — the attacker can append "delete"
  operations, but cannot erase the original operations from the log. Recovery is
  possible by replaying up to the point before the attack.
- **Server-side backup immutability** — backups stored with object-lock or WORM
  (Write Once Read Many) policies. Even a compromised server cannot delete backups
  within the retention window.
- **Audit trail detection** — mass deletion operations trigger anomaly alerts
  (unusual volume of `DeleteNode` operations from a single session).
- **Multi-device resilience** — if the attacker compromises the server but not the
  user's local device, the local copy is unaffected (and vice versa).

**Residual risk:** If the attacker compromises all replicas simultaneously (server
+ all user devices + all backups), data is lost. Mitigated by immutable backups on
separate infrastructure.

#### L5. Encryption Key Loss — The Hard Problem

**Risk:** User loses their encryption key. With zero-knowledge hosting, the data
is permanently unrecoverable. This is the direct tension between security (T4/T5 in
the threat model) and business continuity.

**This is the most important scenario to get right.** Every other data loss scenario
has a straightforward technical mitigation. Key loss is fundamentally a *tradeoff*
between security and recoverability — you cannot maximize both.

**Mitigation by security tier:**

| Tier | Key Loss Mitigation | Recovery Possible? | Security Tradeoff |
|------|--------------------|--------------------|-------------------|
| **Standard** (server-managed keys) | Provider holds keys. "Forgot password" flow resets access | Yes — provider can always recover | Provider can be compelled to decrypt (T4) |
| **Sovereign** (client-held + threshold backup) | Key split across N parties (e.g., 3-of-5 threshold). Any 3 can reconstruct | Yes — if M-of-N parties cooperate | Threshold parties are a trust/compulsion surface |
| **Air-gapped** (HSM/hardware token) | HSM backup to second hardware token stored in a safe | Yes — if backup token exists | Physical security of backup token |
| **Maximum security** (single client key, no backup) | None. Key loss = data loss | **No** | Maximum security, minimum recoverability |

**Recommended default:** Sovereign tier with threshold backup. When a user creates
an account, the system generates their key and immediately performs a key-splitting
ceremony:
1. Key is split into 5 shares (Shamir's Secret Sharing)
2. User keeps 1 share locally
3. 1 share encrypted to the user's recovery email
4. 1 share held by the hosting provider (in escrow)
5. 1 share held by a designated recovery contact (chosen by user)
6. 1 share held by an independent escrow service

Any 3 of 5 shares reconstruct the key. The user can recover even if they lose their
device AND forget their password — as long as 2 of the other 4 parties cooperate.
The hosting provider alone cannot decrypt (they have only 1 of 5 shares).

**Key rotation after recovery:** When a key is recovered via threshold reconstruction,
the system immediately rotates to a new key (re-encrypts all data) and performs a
new splitting ceremony. This limits the exposure window.

**User education:** The system MUST clearly communicate the recovery model during
onboarding. "Maximum security" users must explicitly acknowledge: "I understand that
if I lose my key, my data is permanently unrecoverable, and the provider cannot help."
A checkbox is not enough — require typing a confirmation sentence.

#### L6. Operation Log Growth and Compaction

**Risk:** The append-only log grows without bound. Eventually, compaction is needed
(creating a snapshot and discarding old operations). If compaction discards the wrong
operations, historical data is lost.

**Mitigation:**
- **Compaction creates a snapshot, not a deletion.** The snapshot captures the full
  materialized state at a point in time. Old operations are archived (moved to cold
  storage), not deleted. This means the full history is still *available* on request,
  just not in hot storage.
- **Compaction is verifiable.** The snapshot's content hash must match the hash of
  replaying all operations from genesis. If they don't match, compaction failed and
  the original operations are preserved.
- **Configurable retention.** Users choose how long to keep full operation history
  before compaction. Default: 1 year of full history, then snapshot + archive.
  Compliance-sensitive users can set "never compact" (full history forever, pay for
  storage).

**Residual risk:** If archived operations are lost (storage failure on cold tier),
point-in-time recovery before the snapshot is impossible. The snapshot preserves the
*state* but not the *history* of how it was reached. Mitigated by redundant cold
storage.

#### L7. CRDT Merge Corruption

**Risk:** A bug in the CRDT merge algorithm produces incorrect state after merging
concurrent operations from multiple users. Because CRDT sync propagates to all
replicas, the corruption spreads.

**This is subtle and dangerous** — content addressing verifies individual operations
are intact, but it doesn't verify that the *merge result* is correct. Two valid
operations can produce an invalid merged state if the merge algorithm is buggy.

**Mitigation:**
- **Extensive merge testing** — the CRDT sync phase must include property-based
  tests (Hypothesis/QuickCheck) that verify merge commutativity, associativity,
  and idempotency across thousands of random operation sequences.
- **Merge verification checksums** — after merge, both parties compute a checksum
  of the resulting materialized state. If checksums differ, the merge produced
  divergent results and must be investigated (not silently accepted).
- **Merge rollback** — every merge operation is itself logged. If a merge is
  determined to be incorrect, it can be rolled back by replaying from the pre-merge
  state.
- **Canary nodes** — include known-good reference data in the graph (e.g., a
  "system health" artifact with predetermined content). After merge, verify the
  canary is intact. If not, the merge corrupted data.

**Residual risk:** A merge bug that passes all tests and doesn't trigger checksums
but produces subtly wrong data (e.g., reorders children incorrectly). Mitigated by
user-visible verification (the Lens shows the rendered result — users notice if their
document is garbled).

#### L8. Permission Misconfiguration (Self-Lockout)

**Risk:** User accidentally removes their own OWNER role from an artifact, or a
permission change cascades in a way that locks out all users.

**Mitigation:**
- **OWNER role is irremovable by self.** The system prevents the last OWNER of an
  artifact from revoking their own OWNER role. At least one OWNER must exist at all
  times.
- **SYSTEM principal as emergency backdoor.** An administrator using the SYSTEM
  principal can restore permissions on any artifact. This is the "break glass"
  mechanism.
- **Permission change audit trail.** All role grants and revocations are logged.
  Restoring previous permissions is a matter of replaying the audit log to find
  what was changed and reversing it.
- **Permission change confirmation for destructive actions.** Revoking OWNER,
  changing from private to public, or removing the last EDITOR all require explicit
  confirmation (not just a single API call).

### Backup Strategy (End-State)

#### Backup Tiers

```
Tier 1: Real-time replication
  - CRDT sync to cloud server (every 30 seconds)
  - Multi-device sync (each device is a replica)
  → Protects against: single device failure, brief outages

Tier 2: Server-side snapshots
  - Automated daily snapshots of the operation log store
  - Stored on same provider, different availability zone
  - Retention: 30 daily + 12 monthly + unlimited yearly
  → Protects against: accidental deletion, data corruption

Tier 3: Cross-provider backup
  - Automated daily (or weekly) encrypted export to a different cloud provider
  - Object-locked / WORM storage (immutable for retention period)
  → Protects against: primary provider failure, ransomware, insider threat

Tier 4: User-held export
  - User-initiated full export to local files (standard formats)
  - Encouraged via periodic reminders: "You haven't exported in 30 days"
  → Protects against: complete provider loss, vendor lock-in, all-cloud failure
```

#### Recovery Time Objectives (RTO) and Recovery Point Objectives (RPO)

| Scenario | RPO (max data loss) | RTO (max downtime) | Recovery Method |
|----------|--------------------|--------------------|-----------------|
| Single device failure | 30 seconds (last sync) | 0 (use another device or cloud) | CRDT sync from cloud |
| Server outage | 0 (local-first) | 0 (continue offline) | Sync when server returns |
| Server data loss | 24 hours (last snapshot) | Hours (restore snapshot) | Tier 2 snapshot restore |
| Provider failure | 24 hours (last cross-provider backup) | Hours-days (migrate to new provider) | Tier 3 backup restore |
| Accidental deletion | 0 (append-only log) | Minutes (undo or point-in-time recovery) | Operation log replay |
| Ransomware | 0 (immutable backups) | Hours (restore from WORM backup) | Tier 3 restore |
| Key loss (standard tier) | 0 | Minutes (password reset) | Provider key recovery |
| Key loss (sovereign tier) | 0 | Hours (threshold reconstruction) | M-of-N key shares |
| Key loss (maximum security) | **Total** | **Never** | **Unrecoverable** |

#### Point-in-Time Recovery

The event-sourced architecture provides a capability that most systems don't offer:
**recover to any point in time**, not just the last backup.

```python
# Recover artifact state as of a specific timestamp
state_at_3pm = db.materialize_at(artifact_id, timestamp="2026-02-17T15:00:00Z")

# Or recover to just before a specific operation (e.g., the accidental delete)
state_before_delete = db.materialize_before(artifact_id, operation_id=delete_op_id)
```

This is possible because the operation log is ordered and timestamped. The materializer
can stop replaying at any point. This is analogous to PostgreSQL's Point-in-Time Recovery
(PITR) or git's `checkout` — but built into the core architecture, not bolted on.

### Vendor Independence Guarantees

For customers who worry about the company disappearing:

**1. Open source = no lock-in.**
The entire stack (protocol, schema, database, API, Lenses) is open source. If the
company disappears tomorrow, anyone can:
- Run the server software on their own infrastructure
- Continue developing the software (fork it)
- Read the data format (it's documented and self-describing via JSON-LD)

**2. Standard format export.**
Every artifact can be exported to standard file formats (docx, xlsx, csv, md) at
any time. Even without UAF software, the data is readable.

**3. Operation log is self-contained.**
The operation log is a self-describing, content-addressed data structure. A minimal
replay tool (which is ~200 lines of Python) can rebuild the full graph state from
the raw log. No external service or proprietary runtime required.

**4. No proprietary cloud dependencies.**
The sync protocol (CRDT over WebSocket) runs on any cloud provider or self-hosted
infrastructure. No AWS-specific services, no Google-specific APIs, no Azure lock-in.
The server is a standard Python application that runs anywhere.

**5. Data portability as a legal right.**
Under GDPR Article 20 (right to data portability), EU users have a legal right to
receive their data in a "structured, commonly used and machine-readable format."
UAF's export pipeline satisfies this by design.

### The Trust Equation

Ultimately, business continuity trust comes from the combination of:

```
Trust = Local copies (you have the data)
      + Open source (you can read the format)
      + Standard exports (you can leave anytime)
      + Append-only log (deletion is recoverable)
      + Content addressing (corruption is detectable)
      + CRDT replicas (redundancy is automatic)
      + Immutable backups (ransomware is survivable)
      + Key recovery (lockout is recoverable — with chosen tradeoffs)
```

No single property is sufficient. The combination is what makes the system trustworthy.
The weakest link is key management (L5) — which is why the default should be the
sovereign tier with threshold backup, not the maximum-security tier where key loss is
fatal.

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

### Phase 1: Core Identifiers — `src/uaf/core/node_id.py`

| Type | Description |
|------|-------------|
| `NodeId` | Frozen dataclass wrapping `uuid.UUID`, with `generate()` classmethod |
| `EdgeId` | Same pattern as NodeId |
| `OperationId` | Frozen dataclass wrapping a 64-char hex string (SHA-256 digest) |
| `utc_now()` | Returns timezone-aware UTC datetime |

**Design:** Wrapping UUID in a dataclass gives type safety — you cannot pass a `NodeId`
where an `EdgeId` is expected. `OperationId` is a content hash, NOT a UUID.

**Tests:** `tests/uaf/core/test_node_id.py` (~10 tests: uniqueness, hashability, immutability,
validation of hex strings, utc_now timezone awareness)

---

### Phase 2: Node Types — `src/uaf/core/nodes.py`

**NodeType enum:**
```
ARTIFACT, PARAGRAPH, HEADING, TEXT_BLOCK, CELL, FORMULA_CELL, SHEET,
CODE_BLOCK, TASK, SLIDE, SHAPE, IMAGE
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
| `Cell` | `value: str \| int \| float \| bool \| None` | GridLens |
| `FormulaCell` | `formula: str, cached_value: ...` | GridLens |
| `Sheet` | `title: str, rows: int, cols: int` | GridLens |
| `CodeBlock` | `source: str, language: str` | CodeLens |
| `Task` | `title: str, completed: bool, due_date: ...` | FlowLens |
| `Slide` | `title: str, order: int` | SlideLens |
| `Shape` | `shape_type: str, x, y, width, height` | DrawLens |
| `Image` | `uri: str, alt_text: str, width, height` | All visual Lenses |

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
CONTAINS, REFERENCES, DEPENDS_ON, COMPLIES_WITH, FOLLOWS, LINKED_TO, OWNED_BY
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

All operations share: `parent_ops: tuple[OperationId, ...]` (DAG parents), `timestamp`.

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

### Source Files (13 core/db + 4 format handlers)

| File | Purpose |
|------|---------|
| `src/uaf/core/node_id.py` | NodeId, EdgeId, OperationId, utc_now |
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
tests/uaf/core/test_node_id.py        (~10 tests)
tests/uaf/core/test_nodes.py          (~20 tests)
tests/uaf/core/test_edges.py          (~8 tests)
tests/uaf/core/test_serialization.py  (~15 tests)
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

**Total: ~163 tests**

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
| No thread safety in V1 | Single-writer assumption; add lock in `apply()` later |
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
make check   # ~163 tests passing, zero mypy errors, zero ruff violations
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
| CRDT Sync | Eg-walker merge for concurrent branches | Phase 7 (DAG structure) |
| Fractional Indexing | `fractional-indexing` lib for concurrent reordering | Phase 8 (children_order) |
| JSON-LD Export | Serialize graph to W3C JSON-LD format | Phase 4 (serialization) |
| Type-Specialized Storage | DuckDB for sheets, rope for text | Phase 9 (EAVT as routing layer) |
| Ghost Ingestion Pipeline | Import parsers for all Appendix A formats | Phase 11 (GraphDB API) |
| Access Control | Node/edge-level permissions via security layer | Phase 11 + security layer |
| Vector Embeddings | AI/Graph-RAG metadata on nodes | Phase 2 (NodeMetadata extension) |
| Rust Core (PyO3) | Rewrite hot paths (hashing, indexes, CRDT) in Rust | Profiling data from V1 |

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

### C3. Transclusion + Permissions Interaction

**The problem:** If node X is transcluded into artifacts A and B, and user 1 owns A but
not B, can user 1 edit X? Can user 1 even *see* X when viewing artifact B?

**The tension:**
- Transclusion means "same node, multiple locations." Editing X via A should update X
  everywhere — that's the point.
- But permissions are per-artifact (or per-node). If user 2 owns B and made it private,
  user 1 shouldn't see B's content — but X is shared.

**Options:**

| Approach | How It Works | Tradeoff |
|----------|-------------|----------|
| **Node-level permissions** | Each node has its own ACL. Transclusion doesn't grant access — you need permission on the node itself | Simple model, but transcluding into a private artifact doesn't automatically protect the transcluded content |
| **Context-dependent visibility** | Permissions evaluated relative to the access path (via artifact A vs. via artifact B) | Matches user expectations but complex to implement — the same node has different permissions depending on how you reach it |
| **Copy-on-write for private** | Transcluding into a private artifact creates a copy, not a reference. Updates don't propagate | Sidesteps the problem but undermines transclusion's value |

**Current leaning:** Node-level permissions as the base, with an "inherit from parent"
default. Most nodes inherit permissions from their containing artifact. Transcluded nodes
explicitly set their own ACL when the transclusion crosses a permission boundary. The
security layer enforces this — the database layer just stores the permission metadata.

**V1 impact:** None. V1 has no security layer. The `owner` field on `NodeMetadata` is
informational only. This becomes critical when the security layer is built.

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

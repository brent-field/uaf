# UAF Architecture

**Universal Artifact Format** — a graph-based, AI-native, EU-sovereign knowledge protocol.

---

## Core Thesis

Information is not files. Information is **nodes** (atomic data) connected by **edges**
(semantic relationships). Applications are not silos — they are interchangeable **Lenses**
that view and manipulate the same underlying graph.

A chess game, a spreadsheet, a CAD model, and a text document are all **Artifacts** — root
nodes in a single, unified graph.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       Application Layer                          │
│                        (src/uaf/app/)                            │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │  Lenses   │  │ REST API │  │   MCP    │  │    Format      │  │
│  │ (DocLens, │  │ (FastAPI)│  │  Server  │  │   Handlers     │  │
│  │ GridLens) │  │          │  │  (AI)    │  │ (import/export)│  │
│  └─────┬─────┘  └─────┬────┘  └────┬─────┘  └───────┬────────┘  │
│        │               │            │                │           │
│        └───────────────┴────────────┴────────────────┘           │
│                              │                                   │
├──────────────────────────────┼───────────────────────────────────┤
│                       Security Layer                             │
│                     (src/uaf/security/)                          │
│                              │                                   │
│  ┌──────────────┐  ┌────────┴──────┐  ┌──────────────────────┐  │
│  │ Authenticate │  │  Authorize    │  │   Audit Log          │  │
│  │ (JWT, local) │  │  (RBAC, ACL) │  │   (append-only)      │  │
│  └──────────────┘  └───────────────┘  └──────────────────────┘  │
│                              │                                   │
│                       SecureGraphDB                              │
│                     (policy enforcement)                         │
│                              │                                   │
├──────────────────────────────┼───────────────────────────────────┤
│                       Database Layer                             │
│                        (src/uaf/db/)                             │
│                              │                                   │
│  ┌────────────────┐  ┌──────┴───────┐  ┌──────────────────────┐ │
│  │ OperationLog   │  │ Materializer │  │   EAVT Indexes       │ │
│  │ (append-only   │  │ (replay ops  │  │   (4 covering        │ │
│  │  Merkle DAG)   │  │  into state) │  │    indexes)          │ │
│  └────────────────┘  └──────────────┘  └──────────────────────┘ │
│                              │                                   │
│                           GraphDB                                │
│                      (facade over all)                           │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                          Core                                    │
│                       (src/uaf/core/)                            │
│                                                                  │
│  NodeId, EdgeId, OperationId  │  NodeData union type             │
│  Edge, EdgeType               │  Operation union type            │
│  Serialization, Hashing       │  Error hierarchy                 │
└──────────────────────────────────────────────────────────────────┘
```

**Rule:** Each layer only depends on layers below it and on Core. All application code
talks to `SecureGraphDB`, never directly to `GraphDB`.

---

## Data Model

### Nodes

Every piece of data is a discrete, addressable **node** — a frozen dataclass with typed
fields and shared metadata.

```
NodeMetadata:
    id: NodeId (UUID)
    node_type: NodeType
    created_at: datetime (UTC)
    updated_at: datetime (UTC)
    owner: str | None
    layout: LayoutHint | None
```

**Node types (V1):**

| Type | Fields | Used By |
|------|--------|---------|
| `Artifact` | `title` | All Lenses (top-level container) |
| `Paragraph` | `text, style` | DocLens |
| `Heading` | `text, level` | DocLens |
| `TextBlock` | `text, format` | DocLens |
| `Cell` | `value, row, col` | GridLens |
| `FormulaCell` | `formula, cached_value, row, col` | GridLens |
| `Sheet` | `title, rows, cols` | GridLens |
| `CodeBlock` | `source, language` | DocLens (V1), CodeLens Pro (future) |
| `Task` | `title, completed, due_date` | FlowLens |
| `Slide` | `title, order` | SlideLens |
| `Shape` | `shape_type, x, y, width, height` | DrawLens |
| `Image` | `uri, alt_text, width, height` | Visual Lenses |
| `ArtifactACL` | `default_role, public_read` | Security layer |
| `RawNode` | `raw, original_type` | Schema evolution fallback |

All node types form a union: `type NodeData = Artifact | Paragraph | Heading | ...`

### Edges

Edges are typed, directed relationships between nodes:

```
Edge:
    id: EdgeId
    source: NodeId
    target: NodeId
    edge_type: EdgeType
    created_at: datetime
    properties: tuple[tuple[str, str | int | float | bool], ...]
```

**Edge types:**

| Type | Purpose | Example |
|------|---------|---------|
| `CONTAINS` | Structural tree (parent → child) | Artifact → Paragraph |
| `REFERENCES` | Transclusion (content appears in multiple places) | Doc A → Paragraph in Doc B |
| `DEPENDS_ON` | Dependencies | FormulaCell → Cell, Task → Task |
| `COMPLIES_WITH` | Standards compliance | Design → ISO Standard |
| `FOLLOWS` | Sequencing | Chess Move → Chess Move |
| `LINKED_TO` | User-defined links | Any → Any |
| `OWNED_BY` | Ownership relationships | Node → Owner entity |
| `GRANTS_ROLE` | Permission grants | ACL → Principal |

### Transclusion

Content is referenced, not copied. A paragraph node can be a child of multiple artifacts
via `REFERENCES` edges. Editing the node updates it everywhere — no sync, no copies,
no drift.

---

## Database Layer — Event-Sourced Operation DAG

### Why Event Sourcing?

Every successful collaborative system (Figma, Notion, Linear, git) separates its source
of truth from its query engine. UAF follows this pattern:

- **Source of truth:** Append-only operation log (Merkle DAG)
- **Current state:** Materialized projection with EAVT indexes
- **Benefits:** History for free, content-addressed integrity, natural CRDT merge,
  independently testable components

### Data Flow

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
                    (4 covering indexes)
```

### Operations

All mutations are expressed as immutable operation objects:

| Operation | Purpose |
|-----------|---------|
| `CreateNode` | Add a node to the graph |
| `UpdateNode` | Replace node data (full state, not diff) |
| `DeleteNode` | Mark node as deleted |
| `CreateEdge` | Add a relationship |
| `DeleteEdge` | Remove a relationship |
| `MoveNode` | Re-parent in the containment tree |
| `ReorderChildren` | Change child ordering |

Each operation carries `parent_ops` (DAG parents), `timestamp`, and `principal_id`.
Operations are content-addressed: `OperationId = SHA-256(canonical_json(operation))`.

### Operation Log (Merkle DAG)

Operations form a directed acyclic graph via `parent_ops` references. This provides:

- **Integrity:** Any tampering changes the hash, invalidating all descendants
- **History:** Full audit trail from genesis to current state
- **Sync:** Operations are the natural merge unit for CRDT replication
- **Branching:** Multiple concurrent edits create DAG branches that merge

### EAVT Indexes

Four covering indexes provide O(log n) queries for every access pattern:

| Index | Order | Query Pattern |
|-------|-------|---------------|
| EAVT | Entity → Attribute → Value → Tx | "All attributes of node X" |
| AEVT | Attribute → Entity → Value → Tx | "All nodes with attribute Y" |
| AVET | Attribute → Value → Entity → Tx | "Nodes where attr = val" |
| VAET | Value → Attribute → Entity → Tx | "All references to node X" |

Implemented with `sortedcontainers.SortedList` — O(log n) insert, O(log n + k) range scan.

### GraphDB Facade

`GraphDB` composes all database components behind a single interface:

```python
db = GraphDB()

doc_id = db.create_node(Artifact(meta=..., title="Report"))
h1_id  = db.create_node(Heading(meta=..., text="Summary", level=1))
db.create_edge(Edge(source=doc_id, target=h1_id, edge_type=EdgeType.CONTAINS, ...))

children = db.get_children(doc_id)        # ordered child nodes
tasks    = db.find_by_type(NodeType.TASK)  # all tasks across all artifacts
refs     = db.get_references_to(h1_id)     # who references this node?
history  = db.get_history(doc_id)          # operation history
```

### Performance Profile

| Operation | Complexity | 1K nodes | 100K nodes | 1M nodes |
|-----------|-----------|---------|-----------|---------|
| Get single node | O(1) | <1 μs | <1 μs | <1 μs |
| Get children | O(k) | ~5 μs | ~5 μs | ~5 μs |
| Render document | O(n) tree walk | ~50 μs | ~50 μs | ~50 μs |
| Find by type | O(log n + k) | ~10 μs | ~20 μs | ~30 μs |
| Reverse reference | O(log n + k) | ~10 μs | ~20 μs | ~30 μs |
| Write (full path) | O(log n) | ~50 μs | ~50 μs | ~55 μs |

V1 is in-memory. Scales comfortably to 100K nodes on any machine.

---

## Security Layer

### The Security Sandwich

Every request flows through four stages:

```
Request → Authenticate → Authorize → Execute (GraphDB) → Audit
```

`SecureGraphDB` wraps `GraphDB` with this pipeline. Application code never bypasses it.

### Principals

```
Principal:
    id: PrincipalId
    display_name: str
    roles: frozenset[Role]
    attributes: tuple[tuple[str, str], ...]
```

**Special principals:** `SYSTEM` (bypasses all checks), `ANONYMOUS` (public-read only).

### Authorization: RBAC + Node-Level ACLs

| Role | READ | WRITE | DELETE | GRANT | ADMIN |
|------|------|-------|--------|-------|-------|
| OWNER | Y | Y | Y | Y | Y |
| EDITOR | Y | Y | N | N | N |
| VIEWER | Y | N | N | N | N |
| COMMENTER | Y | N* | N | N | N |

Each artifact has an ACL mapping principals to roles. Permissions **inherit down** the
containment tree — granting EDITOR on an artifact grants EDITOR on all children.

### Audit Trail

Every operation is logged with: who, what, when, on which node, and the outcome
(allowed/denied). The event-sourced operation log provides a tamper-evident history
that satisfies SOX, GDPR, and EU financial reporting requirements.

### V1 Scope

Authentication (local, JWT) + Authorization (RBAC, ACLs) + Audit logging. Encryption
is designed into the interfaces but implemented post-V1.

---

## Application Layer

### The Lens Architecture

A **Lens** is not a UI component — it's a Python class that:
1. **Reads** the graph via `SecureGraphDB`
2. **Renders** a view (HTML, JSON, or text)
3. **Translates** user actions into graph operations

```python
class Lens(Protocol):
    @property
    def lens_type(self) -> str: ...

    @property
    def supported_node_types(self) -> frozenset[NodeType]: ...

    def render(self, db: SecureGraphDB, session: Session,
               artifact_id: NodeId) -> LensView: ...

    def apply_action(self, db: SecureGraphDB, session: Session,
                     artifact_id: NodeId, action: LensAction) -> None: ...
```

**LensView** — the rendered output. Contains `content` (HTML/JSON/text), `content_type`,
and metadata. Every HTML element carries `data-node-id` so the UI can map interactions
back to graph nodes.

**LensAction** — user intent as data. Each action is a frozen dataclass (`InsertText`,
`SetCellValue`, `ReorderNodes`, etc.) that the Lens translates into one or more graph
`Operation` objects. One `LensAction` = one undo-able command.

### V1 Lenses

| Lens | Renders | Node Types |
|------|---------|------------|
| **DocLens** | Documents as semantic HTML (default) or layout-positioned HTML | Artifact, Paragraph, Heading, TextBlock, CodeBlock, Image |
| **GridLens** | Spreadsheets as HTML tables / JSON | Artifact, Sheet, Cell, FormulaCell |

**DocLens view modes:**
- **Semantic** (default) — flowing document with headings, paragraphs, and code blocks
- **Layout** — spatial positioning that approximates the original document appearance,
  using `LayoutHint` metadata populated during PDF/DOCX import. Headers and footers are
  auto-detected and visually tagged. Layout view is read-only.

### Future Lenses

| Lens | Purpose |
|------|---------|
| FlowLens Pro | Project management (Gantt, Kanban) |
| FinanceLens | Accounting, financial reporting |
| SalesLens | CRM, pipeline management |
| SupplyLens | Procurement, inventory |
| ManufacturingLens | Production, quality |
| HRLens | Org chart, payroll |
| LegalLens | Contract management |
| EngineerLens | CAD, BOM, standards compliance |
| MedLens | Clinical workflow, DICOM |
| CodeLens Pro | AST-based code navigation |
| DataLens | Visualization, dashboards |

### REST API (FastAPI)

Three endpoint groups:
- **Graph CRUD** — `/api/artifacts/`, `/api/nodes/`, `/api/edges/`
- **Lens endpoints** — `/api/artifacts/{id}/lens/{type}` (render + actions)
- **Auth endpoints** — `/api/auth/` (login, register, permissions)

All endpoints require JWT authentication. The API is thin — it deserializes requests,
delegates to `SecureGraphDB` or a `Lens`, and serializes responses.

### MCP Server (AI Interface)

Exposes the graph to AI agents via the Model Context Protocol. Tools map directly to
`SecureGraphDB` methods: `create_artifact`, `get_node`, `get_children`, `find_by_type`,
`search`, `render_artifact`, etc.

This is the Graph-RAG vision: AI agents **navigate** typed edges for deterministic
retrieval rather than pattern-matching on flat text.

### Format Handlers

Import/export between the graph and file formats:

| Handler | Import | Export |
|---------|--------|--------|
| Markdown | Heading/paragraph parsing | Semantic markdown output |
| CSV | Row/column → Cell nodes | Cell values → CSV |
| Plain text | Single TextBlock | Concatenated text |
| PDF | Text blocks with layout metadata (bounding boxes, fonts) | Plain text |
| DOCX | Paragraphs, headings, tables with font metadata | DOCX with headings/paragraphs/tables |
| Google Docs | Headings and paragraphs from JSON export | JSON structure |

PDF and DOCX import populates `LayoutHint` on each node with spatial coordinates,
font properties, page numbers, and text rotation. PDF import also detects repeating
headers/footers via heuristic analysis and tags them with `layout.header_footer = True`.

### Text Storage: Semantic Form Requirement

Nodes store text in **semantic/logical form**, not display form. When a source format
splits a word across visual lines (e.g., end-of-line hyphenation in PDFs), the import
handler must reconstruct the complete word:

- **Correct:** `"capability"` (semantic form — the actual word)
- **Incorrect:** `"capa-\nbility"` (display form — how the PDF engine happened to wrap it)

Layout rendering uses `LayoutHint` metadata (bounding box, font, rotation) to
approximate the original visual appearance. The text itself remains clean and semantic,
suitable for search, AI processing, and re-rendering through any Lens.

This principle applies broadly:

| Concern | Stored In | Example |
|---------|-----------|---------|
| Word content | `node.text` | `"capability"` |
| Line breaks | `LayoutHint` coordinates | Bounding box determines visual wrap point |
| Bold/italic | `LayoutHint.font_weight/style` | `"bold"` on the first line's dominant style |
| Rotation | `LayoutHint.rotation` | `-90.0` for 90° CCW text |
| Headers/footers | `LayoutHint.header_footer` | `True` for repeating page-edge text |

Tested via round-trip fidelity: `import → export → compare` must preserve content.

---

## Multi-User Concurrent Editing

An explicit design requirement across all layers:

- **Database:** Event-sourced operation DAG — concurrent operations from different users
  are independent entries that merge naturally (same principle as git)
- **Security:** Stateless per-operation auth — concurrent operations don't conflict at
  the security level
- **Application:** WebSocket push delivers re-rendered `LensView` to all connected clients
  when the graph changes
- **Sync:** CRDT merge protocol (eg-walker) merges concurrent DAG branches

V1 builds the single-user foundation. CRDT sync and WebSocket push are added post-V1.

---

## AI Strategy: Graph-RAG

Traditional AI reads flat text and hallucinates. UAF AI agents **navigate** the graph:

- **Deterministic retrieval:** Follow typed edges (`Invoice → linked_to → Project`) for
  100% accurate context
- **EAVT indexes:** Every query is O(log n) regardless of graph size
- **MCP interface:** Standard protocol for AI agent ↔ graph communication
- **Scoped permissions:** AI agents see only what their principal allows
- **Audit:** Every AI query is logged

### Graph as Training Data

UAF graph data is structurally richer than flat text for AI training:
- Typed relationships (not just token co-occurrence)
- Full edit history (temporal understanding)
- Multi-modal by design (text, data, code, images share the same graph)
- Complete provenance (every node has authorship and lineage)
- GDPR-compliant per-artifact consent for training data contribution

---

## Sovereignty & Compliance

- **EU-sovereign hosting:** Hetzner, OVHcloud, IONOS, Scaleway (not subject to US CLOUD Act)
- **Zero-knowledge encryption:** Server stores encrypted blobs, cannot read data
- **Object-level encryption:** Each node encrypted independently
- **GDPR:** Right to erasure at the node level, consent tracking, audit trails
- **Tamper-evident audit:** Content-addressed operation log detects any modification
- **Open source:** Apache 2.0 core — security is auditable, not trust-me

---

## Technology Choices

| Choice | Why |
|--------|-----|
| **Python 3.13+** | Fastest path to working demo; mypy strict for type safety |
| **sortedcontainers** | EAVT indexes — O(log n) insert/query, pure Python |
| **FastAPI** | Type-safe API, async-ready, auto OpenAPI docs |
| **PyJWT + argon2** | JWT sessions, secure password hashing |
| **MCP SDK** | Standard AI agent protocol |
| **mistune** | Markdown parsing for format handlers |

### Path to Rust

Python V1 isolates hot paths (hashing, indexing, CRDT merge) into self-contained modules.
These can be rewritten in Rust via PyO3 without touching the rest — the same pattern as
Polars, DuckDB, and Automerge.

---

## Key Design Principles

1. **Graph-native:** All data is nodes + edges, never files
2. **Event-sourced:** Append-only operation log is the source of truth
3. **Content-addressed:** Operations are identified by their SHA-256 hash
4. **Frozen data:** All core types are immutable frozen dataclasses
5. **Composition over inheritance:** Nodes have metadata, not a base class
6. **Fail-fast errors:** Mutations raise; queries return None/empty
7. **Layer isolation:** App → Security → Database → Core (never skip)
8. **AI-native:** MCP + EAVT indexes = Graph-RAG from day one
9. **Sovereignty-first:** Open source, EU hosting, zero-knowledge encryption
10. **Local-first:** Works offline; CRDT sync when connected

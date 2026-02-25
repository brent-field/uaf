# UAF Application Layer — Implementation Plan

**Version:** 0.1 (First Draft)
**Date:** 2026-02-17
**Scope:** Application layer (`src/uaf/app/`) — API, Lenses, MCP server, format handlers
**Depends on:** Database layer (`002-database-layer.md`), Security layer (`003-security-layer.md`)

---

## 1. Context

The application layer is the **top** of the UAF stack — everything users and AI agents
interact with:

```
┌─────────────────────────────────────────────────────┐
│                  Application Layer                   │  ◄── THIS PLAN
│                                                     │
│  ┌─────────┐  ┌──────────┐  ┌───────┐  ┌────────┐ │
│  │  Lenses  │  │ REST API │  │  MCP  │  │ Format │ │
│  │ (views)  │  │ (FastAPI)│  │Server │  │Handlers│ │
│  └────┬─────┘  └────┬─────┘  └───┬───┘  └───┬────┘ │
│       │              │            │           │      │
│       └──────────────┴────────────┴───────────┘      │
│                        │                             │
├────────────────────────┼─────────────────────────────┤
│         Security Layer │ (SecureGraphDB)              │
├────────────────────────┼─────────────────────────────┤
│         Database Layer │ (GraphDB)                    │
└────────────────────────┴─────────────────────────────┘
```

**Design constraint:** All application code talks to `SecureGraphDB`, NEVER directly to
`GraphDB`. The security layer is the single entry point for all data access. For V1
development and testing, a `SecureGraphDB` initialized with the `SYSTEM` principal
bypasses all permission checks — giving the same API with zero friction.

**What this plan covers:**
1. **Lens protocol** — The interface all views implement
2. **REST API** — HTTP endpoints for graph operations (FastAPI)
3. **MCP server** — AI agent interface to the graph
4. **DocLens** — First working Lens (document editing/viewing)
5. **GridLens** — Second Lens (spreadsheet)
6. **Format handlers** — Import/export (reconciles with db plan Phase 13)

**What this plan does NOT cover:**
- Full UI rendering (HTML/CSS/JS) — that's a frontend engineering plan
- CodeLens, FlowLens, ScoreLens, SlideLens — future Lenses after V1
- Ghost Ingestion pipeline — future batch import system

**Requirement: Multi-user concurrent editing.** Multiple users MUST be able to edit the
same artifact simultaneously and see each other's changes. The application layer is
responsible for the user-facing side of this: WebSocket connections push updated
`LensView` renders to all connected clients when the graph changes. The database layer
(CRDT sync) handles conflict resolution; the security layer handles per-user authorization;
the application layer handles real-time delivery. V1 builds the Lens and API foundation;
real-time push is added when CRDT sync is available (see §Appendix, "Real-Time
Collaboration").

**V1 demo target:** A user can create a document via the API, add content, view it
through DocLens (rendered as HTML), import a Markdown file, export it back, and an AI
agent can navigate the graph via MCP tools.

---

## 2. Architecture

### The Lens Protocol

A **Lens** is a view of a graph subgraph. It renders an artifact and its children into
a format the user can see and interact with. All Lenses implement the same protocol:

```python
class Lens(Protocol):
    """Protocol for artifact views."""

    @property
    def lens_type(self) -> str:
        """Identifier for this lens (e.g., 'doc', 'grid', 'slide')."""
        ...

    @property
    def supported_node_types(self) -> frozenset[NodeType]:
        """Node types this lens can render."""
        ...

    def render(self, db: SecureGraphDB, session: Session,
               artifact_id: NodeId) -> LensView:
        """Render an artifact into a view."""
        ...

    def apply_action(self, db: SecureGraphDB, session: Session,
                     artifact_id: NodeId, action: LensAction) -> None:
        """Apply a user action (edit, reorder, etc.) to the graph."""
        ...
```

**LensView** — the rendered output:

```
LensView (frozen dataclass):
    lens_type: str
    artifact_id: NodeId
    title: str
    content: str           # HTML, JSON, or plain text depending on lens
    content_type: str      # "text/html", "application/json", "text/plain"
    node_count: int
    rendered_at: datetime
```

**LensAction** — user input translated to graph intent:

```
LensAction = (
    InsertText | DeleteText | FormatText |      # DocLens
    SetCellValue | InsertRow | InsertColumn |    # GridLens
    ReorderNodes | MoveNode | DeleteNode         # All Lenses
)
```

Each `LensAction` is a frozen dataclass. The Lens translates it into one or more
`Operation` objects and applies them via `SecureGraphDB`. This separation means:
- The Lens never touches the graph directly
- Actions are testable independently of rendering
- Different Lenses can share action types (e.g., `ReorderNodes`)

### Why Lenses Are Not Components

A Lens is **not** a UI component. It's a Python class that:
1. **Reads** the graph (via `SecureGraphDB` queries)
2. **Renders** a view (produces HTML/JSON/text)
3. **Translates** user intent into graph operations

The actual UI (browser, terminal, native app) consumes the `LensView.content` and
sends `LensAction` objects back through the API. This keeps Lenses framework-agnostic —
the same `DocLens` works behind a FastAPI endpoint, an MCP tool, or a TUI.

### REST API (FastAPI)

The API has three layers of endpoints:

**Graph CRUD** — direct graph manipulation:
```
POST   /api/artifacts                      → create artifact
GET    /api/artifacts                      → list artifacts
GET    /api/artifacts/{id}                 → get artifact details
DELETE /api/artifacts/{id}                 → delete artifact

GET    /api/nodes/{id}                     → get node
PUT    /api/nodes/{id}                     → update node
DELETE /api/nodes/{id}                     → delete node
GET    /api/nodes/{id}/children            → get children
GET    /api/nodes/{id}/history             → get operation history

POST   /api/edges                          → create edge
DELETE /api/edges/{id}                     → delete edge

GET    /api/search?type=Task               → find by type
GET    /api/search?attr=owner&val=user-1   → find by attribute
```

**Lens endpoints** — view and edit through a Lens:
```
GET    /api/artifacts/{id}/lens/{type}           → render via lens
POST   /api/artifacts/{id}/lens/{type}/action    → apply lens action
GET    /api/artifacts/{id}/lens/{type}/export     → export to file format
POST   /api/artifacts/import                     → import from file
```

**Auth endpoints:**
```
POST   /api/auth/login                     → authenticate, get session token
POST   /api/auth/register                  → create account (V1: local only)
GET    /api/auth/me                        → get current principal
POST   /api/artifacts/{id}/acl             → grant/revoke permissions
GET    /api/artifacts/{id}/acl             → get ACL
```

**Design:** All endpoints require a `Session` (JWT in `Authorization: Bearer` header).
The API layer is thin — it deserializes requests, calls `SecureGraphDB` or a `Lens`,
and serializes responses. No business logic in the API layer.

### MCP Server

The MCP server exposes the graph to AI agents. It maps to the same `SecureGraphDB`
interface as the REST API, but speaks the MCP protocol instead of HTTP.

**MCP Tools** (from db plan, now with security):

```
Tool                            Description
────                            ───────────
create_artifact(title)          Create a new artifact
get_artifact(artifact_id)       Get artifact with children summary
add_child(parent_id, type, ...) Create a child node + CONTAINS edge
get_node(node_id)               Get a single node
get_children(node_id)           Get ordered children
update_node(node_id, fields)    Update node fields
delete_node(node_id)            Delete a node
find_by_type(type)              Find all nodes of a type
search(attribute, value)        Find nodes by attribute value
get_references_to(node_id)      Find all nodes referencing this one
get_history(node_id)            Get operation history
render_artifact(id, lens_type)  Render via a lens (returns HTML/text)
import_file(path, format)       Import a file into the graph
export_file(id, format, path)   Export an artifact to a file
```

**MCP Resources** (read-only views):

```
Resource                        Description
────────                        ───────────
uaf://artifacts                 List of all artifacts
uaf://artifacts/{id}            Artifact details + children
uaf://artifacts/{id}/doc        DocLens rendered view
uaf://artifacts/{id}/grid       GridLens rendered view
```

**Authentication:** The MCP server uses a service-account `Principal` with scoped
permissions per the security plan (§ Appendix, "Authentication Protocol for MCP").
The connecting client provides a token in the MCP session initialization.

### Format Handlers (Reconciled with DB Plan Phase 13)

The db plan's Phase 13 defines `FormatHandler` and `FormatComparator` protocols and
three V1 implementations (Markdown, CSV, plain text). This application plan **does not
duplicate** that work — it builds on top of it:

- Phase 13 source files (`src/uaf/app/formats/`) are implemented during the db plan
- This plan adds the API endpoints that expose import/export
- This plan adds the `LensView` rendering that uses the same node tree

The format handler implementations remain in the db plan because they're tested as
round-trip fidelity tests (proving the data model works). The API wrapper is this plan's
concern.

**Layout metadata:** PDF and DOCX format handlers populate `LayoutHint` on each imported
node with spatial coordinates, font properties, page numbers, and text rotation. The PDF
handler uses PyMuPDF's `get_text("dict")` for rich block-level metadata (bounding boxes,
font family, size, weight, style, color, direction vector). The DOCX handler extracts
section geometry and per-paragraph font info from `python-docx`. PDF import also detects
repeating headers/footers across pages and tags them via `LayoutHint.header_footer`.

**Text storage:** All imported text is stored in *semantic form* — end-of-line hyphenation
is dehyphenated (e.g., `"capa-" + "bility"` → `"capability"`), and display-level line
breaks are captured via `LayoutHint` coordinates rather than embedded in the text. Bold
and italic styling are detected from the first line of each block to avoid dilution by
later lines with different formatting.

---

## 3. Dependencies

```toml
# pyproject.toml additions for application layer
[project]
dependencies = [
    "sortedcontainers>=2.4",     # (from db layer)
    "PyJWT>=2.8",                # (from security layer)
    "argon2-cffi>=23.1",         # (from security layer)
    "mistune>=3.0",              # (from db layer Phase 13)
    "fastapi>=0.115",            # REST API framework
    "uvicorn>=0.34",             # ASGI server
    "mcp>=1.0",                  # MCP server SDK
]

[dependency-groups]
dev = [
    ...existing...,
    "httpx>=0.28",               # FastAPI test client
]
```

All dependencies are FOSS (MIT / Apache 2.0 / BSD).

**Why FastAPI:**
- Type-safe request/response models via Pydantic (aligns with our dataclass approach)
- Async-ready (important for MCP and future WebSocket support)
- Auto-generated OpenAPI docs (useful for VC demo)
- Lightweight — no ORM, no template engine, just HTTP
- Excellent test ergonomics (`TestClient` from `httpx`)

---

## 4. Implementation Phases

Each phase produces a green `make check`. Phases A1-A2 can begin after security layer
Phase S6 is complete. Phases A3-A5 build sequentially.

### Phase A1: Lens Protocol + LensAction Types — `src/uaf/app/lenses/`

**Source files:**

| File | Purpose |
|------|---------|
| `src/uaf/app/lenses/__init__.py` | `Lens` protocol, `LensView`, `LensRegistry` |
| `src/uaf/app/lenses/actions.py` | All `LensAction` types (union type) |

**Lens protocol** — as described in §2.

**LensRegistry** — maps lens type strings to Lens instances:

```python
class LensRegistry:
    def register(self, lens: Lens) -> None: ...
    def get(self, lens_type: str) -> Lens | None: ...
    def available(self) -> list[str]: ...
    def for_node_type(self, node_type: NodeType) -> list[Lens]: ...
```

**LensAction types:**

| Action | Fields | Used By |
|--------|--------|---------|
| `InsertText` | `parent_id, text, position, style` | DocLens |
| `DeleteText` | `node_id` | DocLens |
| `FormatText` | `node_id, style, level` | DocLens |
| `SetCellValue` | `cell_id, value` | GridLens |
| `InsertRow` | `sheet_id, position` | GridLens |
| `InsertColumn` | `sheet_id, position` | GridLens |
| `DeleteRow` | `sheet_id, position` | GridLens |
| `DeleteColumn` | `sheet_id, position` | GridLens |
| `ReorderNodes` | `parent_id, new_order` | All |
| `MoveNode` | `node_id, new_parent_id` | All |
| `DeleteNode` | `node_id` | All |
| `RenameArtifact` | `artifact_id, title` | All |

**Union type:** `type LensAction = InsertText | DeleteText | FormatText | ...`

**Tests:** `tests/uaf/app/test_lens_protocol.py` (~10 tests: registry, LensView
construction, action type completeness)

---

### Phase A2: DocLens — `src/uaf/app/lenses/doc_lens.py`

The first working Lens. Renders a document artifact as HTML.

**Rendering** (`render()`):

1. Get artifact node from graph
2. Get ordered children (headings, paragraphs, text blocks, images)
3. Walk the tree recursively (children can have children)
4. Produce HTML:

```html
<article data-artifact-id="...">
  <h1 data-node-id="...">Quarterly Report</h1>
  <p data-node-id="..." class="body">Revenue grew 15%.</p>
  <h2 data-node-id="...">Details</h2>
  <p data-node-id="..." class="body">...</p>
  <img data-node-id="..." src="blob:abc123" alt="Chart" />
</article>
```

Every rendered element carries `data-node-id` — this is how the UI maps clicks/edits
back to graph nodes. The HTML is semantic, not styled — CSS is the UI layer's concern.

**Supported node types:** `ARTIFACT, PARAGRAPH, HEADING, TEXT_BLOCK, CODE_BLOCK, IMAGE`

**Action handling** (`apply_action()`):

| Action | Graph Operations |
|--------|-----------------|
| `InsertText(parent, text, position, "paragraph")` | `CreateNode(Paragraph)` + `CreateEdge(CONTAINS)` + `ReorderChildren` |
| `InsertText(parent, text, position, "heading")` | `CreateNode(Heading)` + `CreateEdge(CONTAINS)` + `ReorderChildren` |
| `DeleteText(node_id)` | `DeleteNode` + `DeleteEdge(CONTAINS)` |
| `FormatText(node_id, "heading", level=2)` | `UpdateNode` (change node type or properties) |
| `ReorderNodes(parent, new_order)` | `ReorderChildren` |

**Design:** `apply_action` translates one `LensAction` into one or more `Operation`
objects, all applied as a batch. This is the "command grouping" from Appendix C1 of
the db plan — each `LensAction` is one undo-able command.

**Layout rendering** (`render_layout()`):

DocLens also supports a **Layout view** that renders nodes with spatial positioning,
approximating the original document appearance. Uses `LayoutHint` metadata from
`NodeMetadata.layout` (populated during PDF/DOCX import):
- Nodes with coordinates are absolutely positioned within page-sized containers
- Nodes without coordinates fall back to reading-order flow
- Multi-page documents render as separate page divs
- Detected headers/footers are tagged with a distinct CSS class
- Layout view is read-only (no editing actions)

The UI provides a Semantic/Layout toggle in the toolbar that swaps content via HTMX.

**Tests:** `tests/uaf/app/test_doc_lens.py` (~25 tests: semantic rendering, layout
rendering with positioned nodes, multipage, font styles, header/footer tagging,
HTML escaping, editing actions, format changes)

---

### Phase A3: GridLens — `src/uaf/app/lenses/grid_lens.py`

Renders a spreadsheet artifact as an HTML table or JSON grid.

**Rendering** (`render()`):

1. Get artifact node (must be a `Sheet`-containing artifact)
2. Get sheets (children of type `SHEET`)
3. For each sheet, get cells (children of type `CELL`, `FORMULA_CELL`)
4. Build a 2D grid from cell positions (row/column stored in cell properties)
5. Produce HTML table or JSON:

```html
<table data-sheet-id="...">
  <tr>
    <td data-node-id="..." data-row="0" data-col="0">Revenue</td>
    <td data-node-id="..." data-row="0" data-col="1">1500000</td>
  </tr>
  ...
</table>
```

**Supported node types:** `ARTIFACT, SHEET, CELL, FORMULA_CELL`

**Action handling:**

| Action | Graph Operations |
|--------|-----------------|
| `SetCellValue(cell_id, value)` | `UpdateNode(Cell(value=...))` |
| `InsertRow(sheet_id, position)` | N `CreateNode(Cell)` + N `CreateEdge(CONTAINS)` |
| `InsertColumn(sheet_id, position)` | N `CreateNode(Cell)` + N `CreateEdge(CONTAINS)` |
| `DeleteRow(sheet_id, position)` | N `DeleteNode` for cells in that row |
| `DeleteColumn(sheet_id, position)` | N `DeleteNode` for cells in that column |

**Cell positioning:** Each `Cell` and `FormulaCell` needs to know its row and column.
Two options:

| Approach | How | Tradeoff |
|----------|-----|----------|
| **Properties on node** | `Cell(value=..., row=0, col=1)` | Simple, explicit, but row/col change on insert/delete |
| **Edge properties** | `CONTAINS` edge from Sheet carries `(row, 0), (col, 1)` | Position is a relationship, not intrinsic to the cell. Cleaner but more complex |

**Decision:** Properties on the node for V1 (simpler). Row/col are fields on `Cell`
and `FormulaCell`. On insert/delete row, we update affected cells' positions. This is
O(n) per row operation but fine for V1 scale.

**Tests:** `tests/uaf/app/test_grid_lens.py` (~16 tests: render empty sheet, render
with values, render with formulas, set cell value, insert/delete row/column, multi-sheet
rendering)

---

### Phase A4: REST API — `src/uaf/app/api/`

**Source files:**

| File | Purpose |
|------|---------|
| `src/uaf/app/api/__init__.py` | FastAPI app factory, middleware |
| `src/uaf/app/api/dependencies.py` | Dependency injection (SecureGraphDB, Session) |
| `src/uaf/app/api/schemas.py` | Pydantic request/response models |
| `src/uaf/app/api/routes_artifacts.py` | Artifact CRUD endpoints |
| `src/uaf/app/api/routes_nodes.py` | Node CRUD + query endpoints |
| `src/uaf/app/api/routes_lens.py` | Lens render + action endpoints |
| `src/uaf/app/api/routes_auth.py` | Authentication endpoints |
| `src/uaf/app/api/routes_import_export.py` | File import/export endpoints |

**App factory:**

```python
def create_app(db: SecureGraphDB, registry: LensRegistry) -> FastAPI:
    app = FastAPI(title="UAF API", version="1.0")
    # Dependency injection
    app.state.db = db
    app.state.registry = registry
    # Routes
    app.include_router(artifacts_router, prefix="/api/artifacts")
    app.include_router(nodes_router, prefix="/api/nodes")
    app.include_router(lens_router, prefix="/api/artifacts")
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(import_export_router, prefix="/api")
    return app
```

**Pydantic schemas** — request/response models that translate between HTTP JSON and
UAF dataclasses. Examples:

```python
class CreateArtifactRequest(BaseModel):
    title: str
    owner: str | None = None

class NodeResponse(BaseModel):
    id: str
    node_type: str
    fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime

class LensViewResponse(BaseModel):
    lens_type: str
    artifact_id: str
    title: str
    content: str
    content_type: str
    node_count: int
```

**Authentication middleware:** Extract JWT from `Authorization: Bearer` header, resolve
to `Session` via `SecureGraphDB.authenticate(TokenCredentials(...))`. Return 401 if
invalid/expired. The `/api/auth/login` and `/api/auth/register` endpoints are exempt.

**Tests:** `tests/uaf/app/test_api.py` (~25 tests using FastAPI `TestClient`:
artifact CRUD, node CRUD, lens rendering, lens actions, auth flow, import/export,
permission denied responses, query endpoints)

---

### Phase A5: MCP Server — `src/uaf/app/mcp_server.py`

A single-file MCP server that exposes the graph to AI agents.

**Implementation:** Uses the `mcp` Python SDK to register tools and resources.

```python
from mcp.server import Server
from mcp.types import Tool, Resource

def create_mcp_server(db: SecureGraphDB, registry: LensRegistry) -> Server:
    server = Server("uaf")

    @server.tool("create_artifact")
    async def create_artifact(title: str) -> dict: ...

    @server.tool("get_node")
    async def get_node(node_id: str) -> dict: ...

    @server.tool("get_children")
    async def get_children(node_id: str) -> list[dict]: ...

    # ... all tools from §2 MCP Tools table

    @server.resource("uaf://artifacts")
    async def list_artifacts() -> str: ...

    @server.resource("uaf://artifacts/{id}")
    async def get_artifact_resource(id: str) -> str: ...

    return server
```

**Tool responses:** Return JSON-serialized node/edge data using the same `node_to_dict`
functions from the core serialization module. This means AI agents see the same data
format regardless of whether they use MCP or REST.

**Session management:** The MCP server authenticates once during connection setup.
All tool calls use the same session. The principal is determined by the token provided
during initialization (or defaults to a configured service account).

**Tests:** `tests/uaf/app/test_mcp_server.py` (~15 tests: tool registration, create
artifact via tool, query via tool, render via tool, import/export via tool, error
handling for non-existent nodes)

---

### Phase A6: App Exports + Integration Tests

**`src/uaf/app/__init__.py`** — wire up `__all__`

**`tests/uaf/app/test_integration.py`** — end-to-end scenario tests:

1. **Document workflow:** Register user → login → create artifact → add content via
   DocLens actions → render → verify HTML output → export as Markdown → verify content
2. **Spreadsheet workflow:** Create artifact → add sheet → add cells via GridLens →
   set formula → render → export as CSV → verify values
3. **Import → Lens → Export:** Import Markdown file → render via DocLens → verify HTML
   contains all content → export back to Markdown → round-trip verify
4. **MCP agent workflow:** Create artifact via MCP tool → add content → query via
   `find_by_type` → render via `render_artifact` tool → verify output
5. **Multi-user via API:** User 1 creates artifact → grants EDITOR to user 2 → user 2
   edits via API → user 3 gets 403 → audit log shows all actions
6. **Cross-lens consistency:** Create document via DocLens → same artifact rendered via
   API JSON endpoint → both views show same content

---

## 5. File Summary

### Source Files (10)

| File | Purpose |
|------|---------|
| `src/uaf/app/lenses/__init__.py` | Lens protocol, LensView, LensRegistry |
| `src/uaf/app/lenses/actions.py` | LensAction union type + all action dataclasses |
| `src/uaf/app/lenses/doc_lens.py` | DocLens (document rendering + editing) |
| `src/uaf/app/lenses/grid_lens.py` | GridLens (spreadsheet rendering + editing) |
| `src/uaf/app/api/__init__.py` | FastAPI app factory |
| `src/uaf/app/api/dependencies.py` | DI for SecureGraphDB, Session |
| `src/uaf/app/api/schemas.py` | Pydantic request/response models |
| `src/uaf/app/api/routes_artifacts.py` | Artifact CRUD endpoints |
| `src/uaf/app/api/routes_nodes.py` | Node CRUD + query endpoints |
| `src/uaf/app/api/routes_lens.py` | Lens render + action endpoints |
| `src/uaf/app/api/routes_auth.py` | Auth endpoints |
| `src/uaf/app/api/routes_import_export.py` | File import/export endpoints |
| `src/uaf/app/mcp_server.py` | MCP server (tools + resources) |
| `src/uaf/app/__init__.py` | Public exports |

Note: Format handler files (`src/uaf/app/formats/`) are implemented in db plan Phase 13.

### Test Files (6)

```
tests/uaf/app/test_lens_protocol.py     (~10 tests)
tests/uaf/app/test_doc_lens.py          (~18 tests)
tests/uaf/app/test_grid_lens.py         (~16 tests)
tests/uaf/app/test_api.py               (~25 tests)
tests/uaf/app/test_mcp_server.py        (~15 tests)
tests/uaf/app/test_integration.py       (~6 scenario tests)
```

Note: `tests/uaf/app/test_roundtrip.py` (~25 tests) is in db plan Phase 13.

**App layer total: ~90 tests**
**Combined project total: ~168 (db plan, includes core + round-trip) + ~80 (security) + ~90 (app) = ~338 tests**

---

## 6. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Lens is a Protocol, not a base class | Structural typing — any class with the right methods is a Lens. Easier to test with fakes |
| LensView returns HTML/JSON string, not a component tree | Framework-agnostic — same Lens works with FastAPI, MCP, CLI, or native app |
| LensAction as separate frozen dataclasses | Each action is data, not a method call. Testable, serializable, groupable for undo |
| One LensAction = one undo command | Aligns with db plan Appendix C1 (semantic undo with command grouping) |
| FastAPI over Flask/Django | Type-safe, async-ready, auto-OpenAPI docs, no ORM baggage |
| MCP server in single file | It's a thin wrapper — no need for a sub-package. Easy to read top-to-bottom |
| Pydantic schemas separate from core dataclasses | API schemas are HTTP concerns (JSON serialization, validation). Core dataclasses are domain concerns. No coupling |
| `data-node-id` on all HTML elements | Enables the UI to map user interactions back to graph nodes without a separate mapping layer |
| Cell row/col as node fields (not edge properties) | Simpler for V1. Edge properties are better long-term but add complexity |
| Format handlers stay in db plan | They prove the data model (round-trip tests). The app plan wraps them in API endpoints |

---

## 7. Verification

### Per-phase gate
```bash
make check   # ruff check + mypy strict + pytest — must pass after every phase
```

### Testing strategy

**Level 1 — Unit tests (Phases A1-A3):** Lens protocol, action types, rendering
logic, action-to-operation translation. Uses `GraphDB` directly (or `SecureGraphDB`
with SYSTEM principal) — no HTTP.

**Level 2 — API tests (Phase A4):** FastAPI `TestClient` sends HTTP requests, verifies
responses. Tests auth flow, CRUD operations, lens endpoints, permission enforcement.
In-memory `SecureGraphDB` — no external services.

**Level 3 — MCP tests (Phase A5):** MCP tool calls, verify correct graph operations
and responses. In-memory, no network.

**Level 4 — Integration tests (Phase A6):** Full workflow scenarios crossing all layers.
User registers → logs in → creates content → views via Lens → exports → re-imports.

### After all phases
```bash
make check   # ~338 total tests passing, zero mypy errors, zero ruff violations
```

---

## 8. Alignment with Database Layer

| DB Layer Feature | App Layer Usage |
|-----------------|----------------|
| `GraphDB` / `SecureGraphDB` | All Lenses and API endpoints use this as their data source |
| `NodeData` union type | Lenses `match` on node types to render appropriate HTML/elements |
| `children_order` | DocLens renders children in order; GridLens uses row/col positioning |
| Operations (`CreateNode`, etc.) | `LensAction.apply_action()` translates actions into operations |
| `get_history()` | API exposes operation history per node |
| `find_by_type()` / `find_by_attribute()` | API search endpoints delegate directly |
| EAVT indexes | Used indirectly — all queries go through `SecureGraphDB` which uses `QueryEngine` |
| `FormatHandler` protocol (Phase 13) | Import/export API endpoints wrap format handlers |
| `BlobStore` | Image rendering in DocLens uses `blob:<id>` URIs; API serves blob content |
| Schema evolution (`RawNode`) | Lenses render `RawNode` as a generic "unknown type" block |

### Changes needed in the database layer

1. **Cell positioning** — Add `row: int` and `col: int` fields to `Cell` and
   `FormulaCell` node types in Phase 2. Currently cells have no position information.

2. **`descendants()` method** — Add `GraphDB.descendants(node_id) -> set[NodeId]` to
   Phase 11. Already referenced in db plan §2 (Query Scope) but not in the Phase 11
   method list. Needed by Lenses for recursive tree rendering.

3. **Blob serving** — Add `GraphDB.get_blob(blob_id: BlobId) -> bytes | None` to
   Phase 11. DocLens needs to reference blob URIs; the API needs to serve blob content.

---

## 9. Alignment with Security Layer

| Security Feature | App Layer Usage |
|-----------------|----------------|
| `SecureGraphDB` | ALL app code uses this, never raw `GraphDB` |
| `Session` | Every API request extracts session from JWT header |
| `authenticate()` | Login endpoint calls this |
| Permission enforcement | API returns 403 when `PermissionDeniedError` is raised |
| Audit trail | API exposes audit log for artifact owners |
| `SYSTEM` principal | Used in tests and internal operations (format import/export) |
| `ANONYMOUS` principal | Used for public artifacts (no auth required) |
| `ACL` management | API endpoints for grant/revoke permissions |
| Role-based access | Lens actions check write permission before applying |

### Changes needed in the security layer

1. **Session from token** — Ensure `SecureGraphDB.authenticate(TokenCredentials(...))`
   returns a `Session` that can be used for subsequent calls. The API middleware will
   call this on every request.

2. **SYSTEM session factory** — Add a convenience method
   `SecureGraphDB.system_session() -> Session` for internal operations (import/export,
   tests). Avoids needing to create a SYSTEM principal manually.

---

## 10. Future Lenses (Not in V1)

| Lens | Purpose | Key Node Types | Depends On |
|------|---------|---------------|------------|
| **SlideLens** | Presentation viewer/editor | Slide, Shape, TextBlock, Image | DocLens patterns + LayoutHint |
| **CodeLens** | IDE / code viewing | CodeBlock + AST sub-nodes | tree-sitter integration |
| **FlowLens** | Project management (Gantt/Kanban) | Task + DEPENDS_ON edges | Temporal edge rendering |
| **ScoreLens** | Sheet music viewer | Custom music nodes | music21 integration |
| **DrawLens** | Diagram/whiteboard editor | Shape + LINKED_TO edges | Canvas rendering |
| **ChessLens** | Chess game viewer | MoveNode + FOLLOWS edges | python-chess integration |
| **DataLens** | Data visualization (charts) | Sheet, Cell | Chart rendering library |
| **SupplyLens** | Procurement / inventory | PurchaseOrder, Vendor, Product | ERP node types (see `005` Appendix B) |
| **SalesLens** | CRM / pipeline management | Customer, Opportunity, SalesOrder | ERP node types (see `005` Appendix B) |
| **ManufacturingLens** | Production / quality | ProductionOrder, Batch, WorkCenter | ERP node types (see `005` Appendix B) |
| **HRLens** | Org chart / payroll | Employee, Position, Department | ERP node types (see `005` Appendix B) |

Each future Lens follows the same pattern: implement the `Lens` protocol, register in
`LensRegistry`, add API routes automatically via the existing `/lens/{type}` endpoint
pattern. No infrastructure changes needed — just a new Python class.

---

## Appendix: Open Questions

### Frontend Technology

The application plan deliberately avoids choosing a frontend framework. The Lenses
produce HTML/JSON; something needs to render that to the user. Options:

| Approach | Pros | Cons |
|----------|------|------|
| **HTMX + server-rendered HTML** | Simplest, no build step, works with DocLens HTML output | Limited interactivity, no offline |
| **React / Next.js** | Rich interactivity, large ecosystem | Heavy, requires separate frontend build |
| **Svelte / SvelteKit** | Lighter than React, good DX | Smaller ecosystem |
| **Textual (Python TUI)** | Pure Python, impressive for demo, no browser needed | Limited to terminal |
| **Tauri (Rust + webview)** | Native desktop, small binary | Adds Rust dependency |

**Current leaning:** HTMX for V1 demo. DocLens already produces HTML — HTMX can swap
it in-place with zero JavaScript. Add React/Svelte when rich interactivity is needed
(GridLens cell editing, SlideLens drag-and-drop).

### Real-Time Collaboration (WebSocket) — Required, Post-V1

Multi-user concurrent editing is an explicit project requirement. V1 uses request/response
(REST) as the foundation. The real-time layer adds:

- **WebSocket connection per user** — authenticated session, scoped to one or more artifacts
- **Server pushes `LensView` updates** — when any user's operation changes the graph,
  all connected clients viewing that artifact receive a re-rendered `LensView`
- **Conflict resolution via CRDT** — the db layer's CRDT sync phase (Appendix B of
  `002-database-layer.md`) merges concurrent operations. The app layer does NOT need
  its own conflict resolution — it just re-renders after merge
- **Presence indicators** — which users are currently viewing/editing (lightweight
  metadata, not stored in the graph)

**Implementation path:**
1. DB layer: CRDT sync (operation DAG merge) — this is the hard part
2. App layer: WebSocket endpoint per artifact, push `LensView` on graph change
3. App layer: Presence tracking (in-memory, ephemeral)
4. Frontend: WebSocket client, optimistic UI updates

FastAPI supports WebSocket natively. The Lens protocol's `render()` method works for
both request/response and push — call it whenever the graph changes, push the new
`LensView` to all connected clients viewing that artifact.

**Why this works:** The architecture was designed for this from day one. The event-sourced
operation DAG means concurrent edits are independent operations that merge, not conflicting
writes to shared mutable state. The Lens protocol's separation of rendering from data
means re-rendering after a merge is just another `render()` call.

### Rich Text Editing

DocLens V1 produces semantic HTML. Rich text editing (bold, italic, lists, etc.) in the
browser typically requires a library like ProseMirror, TipTap, or Slate. These libraries
manage their own document model — integrating them means syncing between the editor's
model and UAF's graph model.

Options:
- **One-way:** Editor produces HTML, we parse it back to nodes on save (lossy)
- **Two-way sync:** Editor operations map to LensActions in real-time (complex but correct)
- **Custom editor:** Build a simple contenteditable wrapper that sends LensActions directly
  (limited features but full control)

**Current leaning:** Custom editor for V1 (simple paragraph/heading editing). ProseMirror
integration as a future phase when rich formatting is needed.

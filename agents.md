# UAF Project — Document Index

This file is a table of contents for all project documentation. It serves as the starting
point for AI agents and collaborators navigating the UAF project.

---

## Architecture

| Document | Path | Summary |
|----------|------|---------|
| **Architecture Overview** | [docs/architecture.md](docs/architecture.md) | System architecture, data model, layer design, technology choices, and design principles |
| **UI Guide** | [docs/ui-guide.md](docs/ui-guide.md) | How to run and use the HTMX web frontend (login, dashboard, document editor, spreadsheet viewer, import/export) |

---

## Planning Documents

| # | Document | Path | Summary |
|---|----------|------|---------|
| 001 | **Initial Vision** | *private — see uaf-confidential repo* | Strategic blueprint — Global Object Graph, Lens architecture, domain extensions, AI strategy, EU sovereignty positioning |
| 002 | **Database Layer** | [docs/plans/002-database-layer.md](docs/plans/002-database-layer.md) | Core data model + database implementation — event-sourced operation DAG, EAVT indexes, node/edge types, serialization, GraphDB facade. 13 implementation phases. Appendices: undo/redo, CRDT sync, Rust migration, AI training data, business continuity |
| 003 | **Security Layer** | [docs/plans/003-security-layer.md](docs/plans/003-security-layer.md) | Authentication, authorization (RBAC + ACLs), audit logging, SecureGraphDB wrapper. 6 implementation phases. Appendices: encryption roadmap, state-sponsored threat model |
| 004 | **Application Layer** | [docs/plans/004-application-layer.md](docs/plans/004-application-layer.md) | Lens protocol, DocLens, GridLens, REST API (FastAPI), MCP server, format handlers. 6 implementation phases. Appendices: frontend options, real-time collaboration, rich text editing |
| 005 | **Business Plan** | *private — see uaf-confidential repo* | Products, distribution, open-source strategy, revenue model, market segments, competitive landscape |
| 006 | **Shapes Support** | [docs/plans/006-shapes-support.md](docs/plans/006-shapes-support.md) | Shape node types (lines, rectangles, circles, paths) for diagramming and annotation |
| 007 | **Layout Fidelity Tests** | [docs/plans/007-layout-fidelity-tests.md](docs/plans/007-layout-fidelity-tests.md) | Ground-truth PDF fidelity test suite comparing extracted layout properties against known PDF structure |
| 008 | **Layout Inspector UI** | [docs/plans/008-layout-inspector-ui.md](docs/plans/008-layout-inspector-ui.md) | Interactive typographic debugging — hover tooltips, click-to-inspect panel, keyboard shortcuts for the Layout view |
| 009 | **Persistence** | [docs/plans/009-persistence.md](docs/plans/009-persistence.md) | JSONL journal persistence — JournaledGraphDB wrapper, SecurityStore, blob storage, dev-mode store delete, benchmarks |

---

## Implementation Status

| Layer | Status | Phases | Tests |
|-------|--------|--------|-------|
| **Core** (`src/uaf/core/`) | Not started | Phases 0-6 (in db plan) | ~70 planned |
| **Database** (`src/uaf/db/`) | Not started | Phases 7-12 (in db plan) | ~73 planned |
| **Round-trip formats** (`src/uaf/app/formats/`) | Not started | Phase 13 (in db plan) | ~25 planned |
| **Security** (`src/uaf/security/`) | Not started | Phases S1-S6 | ~80 planned |
| **Application** (`src/uaf/app/`) | Not started | Phases A1-A6 | ~90 planned |
| **Total** | | | **~338 planned** |

---

## Key Concepts

| Concept | Description | Defined In |
|---------|-------------|------------|
| **Artifact** | Top-level container node (document, spreadsheet, chess game, CAD model) | 002 §1 |
| **Node** | Atomic, addressable unit of data (paragraph, cell, task, image) | 002 §Phase 2 |
| **Edge** | Typed, directed relationship between nodes | 002 §Phase 3 |
| **Operation** | Immutable mutation record (CreateNode, UpdateNode, etc.) | 002 §Phase 5 |
| **Operation DAG** | Append-only Merkle DAG of operations — the source of truth | 002 §Phase 7 |
| **EAVT Index** | Four covering indexes for O(log n) queries | 002 §Phase 9 |
| **GraphDB** | Facade composing all database components | 002 §Phase 11 |
| **JournaledGraphDB** | Persistence wrapper — write-ahead JSONL journal + replay on startup | 009 |
| **SecurityStore** | Persist/replay security events (principals, ACLs, audit) | 009 |
| **SecureGraphDB** | Security wrapper enforcing auth, ACLs, and audit | 003 §2 |
| **Principal** | Authenticated identity (user or service account) | 003 §2 |
| **ACL** | Per-artifact access control list mapping principals to roles | 003 §2 |
| **Lens** | View protocol — renders a graph subgraph and translates user actions | 004 §2 |
| **LensView** | Rendered output from a Lens (HTML, JSON, or text) | 004 §2 |
| **LensAction** | User intent as data — frozen dataclass translated to graph operations | 004 §Phase A1 |
| **MCP Server** | AI agent interface to the graph via Model Context Protocol | 004 §Phase A5 |
| **Format Handler** | Import/export protocol for file formats (Markdown, CSV, etc.) | 002 §Phase 13 |
| **Ghost Ingestion** | Migration service converting legacy data into the UAF graph | 005 §6 |
| **Vault** | Managed UAF hosting instance for an organization | 005 §4 |
| **Transclusion** | Content referenced, not copied — updates propagate everywhere | 002 §2, architecture |
| **Graph-RAG** | AI agents navigate typed edges for deterministic retrieval | 001 §5, architecture |
| **CRDT Sync** | Conflict-free replication for multi-user concurrent editing | 002 Appendix B |

---

## Cross-References

These connections span multiple documents:

- **Multi-user editing** — Requirement defined in 002 §1, security implications in 003 §1, WebSocket delivery in 004 §Appendix, CRDT sync protocol in 002 Appendix B
- **CRDT sync** — Architecture in 002 Appendix B, security statelessness in 003 §1, WebSocket push in 004 §Appendix
- **Rust migration** — 4-stage path in 002 Appendix B, technology risk in 005 §13
- **AI training data** — Structural advantages in 002 Appendix D, marketplace in 005 §7
- **Business continuity** — 8 loss scenarios in 002 Appendix E, key management in 003 Appendix B (T5)
- **State-sponsored threats** — 8 threat categories in 003 Appendix B, sovereignty positioning in 005 §4
- **ERP replacement** — Module mapping and 9 industry applications in 005 Appendix B, FlowLens Pro in 004 §10
- **Format handlers** — Protocol in 002 Phase 13, API endpoints in 004 Phase A4, Ghost Ingestion in 005 §6

---

## Project Configuration

| File | Purpose |
|------|---------|
| [CLAUDE.md](CLAUDE.md) | AI agent instructions — project overview, tech stack, commands, coding conventions |
| [pyproject.toml](pyproject.toml) | Python project config, dependencies, tool settings |
| [Makefile](Makefile) | `make install`, `make test`, `make lint`, `make format`, `make check`, `make bench`, `make reset-store` |
| [.pre-commit-config.yaml](.pre-commit-config.yaml) | Pre-commit hooks (Ruff + Mypy) |

---

## Quick Start for Agents

1. Read this file for orientation
2. Read [CLAUDE.md](CLAUDE.md) for coding conventions and commands
3. Read [docs/architecture.md](docs/architecture.md) for the system design
4. Read the relevant plan for your task:
   - Data model / database work → [002](docs/plans/002-database-layer.md)
   - Security / auth work → [003](docs/plans/003-security-layer.md)
   - API / Lens / MCP work → [004](docs/plans/004-application-layer.md)
   - Business / product questions → see uaf-confidential repo
5. Implementation starts at Phase 0 (project rename) in the database plan

---

## Multi-Repo Structure

UAF is split across three repositories:
- **uaf** (this repo, public) — open-source core: graph primitives, database, security, application layer, lenses
- **uaf-confidential** (private) — business strategy, vision docs, investor materials (docs 001, 005)
- **uaf-premium** (private) — proprietary lenses, enterprise integrations, commercial features

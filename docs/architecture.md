# V1 Architecture

## Three-Layer Design

```
┌─────────────────────────────────┐
│        Application Layer        │
│   API endpoints, Lens UIs       │
│         (src/udf/app/)          │
├─────────────────────────────────┤
│         Security Layer          │
│   Auth, encryption, ACL         │
│       (src/udf/security/)       │
├─────────────────────────────────┤
│         Database Layer          │
│   Storage, queries, CRDT sync   │
│          (src/udf/db/)          │
└─────────────────────────────────┘

         Core (src/udf/core/)
   Graph primitives used by all layers
```

## Core Layer

Shared graph primitives that all other layers depend on:

- **Node models** — Atomic data units (text, data cells, functions, etc.)
- **Edge models** — Semantic relationships between nodes
- **Schema definitions** — JSON-LD / RDF-star compatible type system
- **Graph operations** — Traversal, querying, transclusion

## Database Layer

Handles persistence and synchronization:

- **Storage backends** — Pluggable persistence (local, cloud)
- **Query engine** — Graph traversal and pattern matching
- **CRDT sync** — Conflict-free replication for offline-first collaboration
- **Content addressing** — Merkle tree-based integrity verification

## Security Layer

Controls access and protects data:

- **Authentication** — Identity verification (DID-based)
- **Encryption** — Object-level and zero-knowledge encryption
- **Access control** — Node/edge-level permissions
- **Audit trails** — Immutable operation logs

## Application Layer

User-facing interfaces built on top of the graph:

- **API** — Graph manipulation endpoints
- **Lenses** — Interchangeable views (DocLens, GridLens, etc.)
- **Migration** — "Ghost Ingestion" pipeline for legacy data import

## Design Constraints

- Each layer only depends on layers below it and on Core
- Core has no dependencies on other layers
- All cross-layer communication goes through defined interfaces

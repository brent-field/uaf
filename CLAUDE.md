# Universal Artifact Format (UAF)

## Project Overview

UAF is a graph-based, AI-native knowledge protocol that replaces file-based legacy software with a Global Object Graph. Information is stored as **Atomic Nodes** (data) connected by semantic **Edges** (logic). Applications are interchangeable **Lenses** that view and manipulate the same underlying graph.

The V1 pilot targets a VC demo with three layers:
- **Database layer** (`src/uaf/db/`) — Storage, persistence, queries, CRDT sync
- **Security layer** (`src/uaf/security/`) — Auth, encryption, access control
- **Application layer** (`src/uaf/app/`) — API endpoints, Lens interfaces

Core graph primitives live in `src/uaf/core/`.

## Multi-Repo Structure

UAF is split across three repositories:
- **uaf** (this repo, public) — open-source core: graph primitives, database, security, application layer, lenses
- **uaf-confidential** (private) — business strategy, vision docs, investor materials
- **uaf-premium** (private) — proprietary lenses, enterprise integrations, commercial features

All open-source code and technical docs belong here. Business strategy and proprietary extensions go in the private repos. When in doubt, default to this repo.

## Tech Stack

- **Language:** Python 3.13+
- **Package manager:** UV
- **Linting:** Ruff
- **Type checking:** Mypy (strict mode)
- **Testing:** Pytest
- **Pre-commit:** Ruff + Mypy hooks

## Commands

```bash
make install       # uv sync — install all dependencies
make test          # Run pytest
make lint          # Ruff check + mypy
make format        # Ruff format (auto-fix)
make check         # lint + test combined
make bench         # Run persistence performance benchmarks
make reset-store   # Delete the store directory (dev-only)
```

## Project Structure

```
src/uaf/
  core/       # Graph primitives, node/edge models, schema
  db/         # Database layer — storage, persistence, queries, sync
  security/   # Security layer — auth, encryption, access control
  app/        # Application layer — API, lenses
tests/        # Mirrors src/uaf/ structure
docs/         # Architecture and design documents
```

## Coding Conventions

- All code must pass `ruff check` and `mypy --strict`
- Use type annotations on all function signatures
- Write tests for all public functions in the mirrored `tests/` directory
- Line length: 99 characters
- Import sorting: isort-compatible (handled by ruff)
- Prefer dataclasses or Pydantic models for data structures
- Keep modules focused — one responsibility per file

## Key Design Principles

- **Graph-native:** All data is nodes + edges, not files
- **Atomic Nodes:** Every piece of data is a discrete, addressable node
- **Transclusion:** Content is referenced, not copied — updates propagate
- **AI-native:** Designed for Graph-RAG — AI agents navigate the graph, not read text
- **Sovereignty:** EU/GDPR-compliant, local-first with CRDT sync

## Workflow

- **Use worktrees for feature work:** When implementing changes, use an isolated git worktree so the main working tree stays clean.

## Key Files

- `agents.md` — Document index and table of contents for all project docs
- `docs/architecture.md` — System architecture and design principles
- `docs/plans/` — Planning documents (numbered sequentially)
- `pyproject.toml` — Project config, dependencies, tool settings

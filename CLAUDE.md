# Universal Data Format (UDF)

## Project Overview

UDF is a graph-based, AI-native knowledge protocol that replaces file-based legacy software with a Global Object Graph. Information is stored as **Atomic Nodes** (data) connected by semantic **Edges** (logic). Applications are interchangeable **Lenses** that view and manipulate the same underlying graph.

The V1 pilot targets a VC demo with three layers:
- **Database layer** (`src/udf/db/`) — Storage, persistence, queries, CRDT sync
- **Security layer** (`src/udf/security/`) — Auth, encryption, access control
- **Application layer** (`src/udf/app/`) — API endpoints, Lens interfaces

Core graph primitives live in `src/udf/core/`.

## Tech Stack

- **Language:** Python 3.13+
- **Package manager:** UV
- **Linting:** Ruff
- **Type checking:** Mypy (strict mode)
- **Testing:** Pytest
- **Pre-commit:** Ruff + Mypy hooks

## Commands

```bash
make install    # uv sync — install all dependencies
make test       # Run pytest
make lint       # Ruff check + mypy
make format     # Ruff format (auto-fix)
make check      # lint + test combined
```

## Project Structure

```
src/udf/
  core/       # Graph primitives, node/edge models, schema
  db/         # Database layer — storage, persistence, queries, sync
  security/   # Security layer — auth, encryption, access control
  app/        # Application layer — API, lenses
tests/        # Mirrors src/udf/ structure
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

## Key Files

- `gemini_plan_1.md` — Full vision document and strategic blueprint
- `docs/architecture.md` — V1 three-layer architecture
- `pyproject.toml` — Project config, dependencies, tool settings

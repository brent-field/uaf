# Universal Data Format (UDF)

A graph-based, AI-native knowledge protocol that replaces file-based legacy software with a Global Object Graph.

## Architecture

UDF is structured in three layers:

- **Core** (`src/udf/core/`) — Graph primitives, node/edge models, schema definitions
- **Database** (`src/udf/db/`) — Storage, persistence, queries, CRDT sync
- **Security** (`src/udf/security/`) — Authentication, encryption, access control
- **Application** (`src/udf/app/`) — API endpoints, Lens interfaces

See [docs/architecture.md](docs/architecture.md) for details and [gemini_plan_1.md](gemini_plan_1.md) for the full vision document.

## Getting Started

Requires [UV](https://docs.astral.sh/uv/) and Python 3.13+.

```bash
# Install dependencies
make install

# Run tests
make test

# Lint and type-check
make lint

# Auto-format
make format

# Run all checks
make check
```

## Development

This project uses:
- **UV** for package management
- **Ruff** for linting and formatting
- **Mypy** (strict) for type checking
- **Pytest** for testing
- **Pre-commit** hooks for CI hygiene

```bash
# Set up pre-commit hooks
uv run pre-commit install
```

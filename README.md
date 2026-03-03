# Universal Artifact Format (UAF)

A graph-based, AI-native knowledge protocol that replaces file-based legacy software with a Global Object Graph.

## Architecture

UAF is structured in four layers:

- **Core** (`src/uaf/core/`) — Graph primitives, node/edge models, schema definitions
- **Database** (`src/uaf/db/`) — Storage, persistence, queries, CRDT sync
- **Security** (`src/uaf/security/`) — Authentication, encryption, access control
- **Application** (`src/uaf/app/`) — API endpoints, Lens interfaces

See [agents.md](agents.md) for a full document index and [docs/architecture.md](docs/architecture.md) for the system design.

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

## License

This project is licensed under the Apache License 2.0 — see [LICENSE](LICENSE) for details.

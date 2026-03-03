# Copilot Instructions

See [CLAUDE.md](../CLAUDE.md) for comprehensive project instructions, coding conventions, and architecture details.

## Quick Reference

- **Language:** Python 3.13+
- **Package manager:** UV (`uv sync` to install)
- **Test:** `make test` (pytest)
- **Lint:** `make lint` (ruff + mypy strict)
- **Format:** `make format` (ruff format)
- **Structure:** `src/uaf/` with `core/`, `db/`, `security/`, `app/` submodules
- **Tests:** Mirror structure in `tests/`

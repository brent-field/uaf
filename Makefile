.PHONY: install test test-visual lint format check bench reset-store

install:
	uv sync

test:
	uv run pytest

test-visual:
	uv run pytest -m playwright -v

lint:
	uv run ruff check src tests
	uv run mypy src

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

check: lint test

bench:
	uv run pytest tests/uaf/db/test_journal_bench.py -v

reset-store:
	rm -rf store/
	@echo "Store wiped."

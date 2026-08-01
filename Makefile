.PHONY: sync test lint fix format clean

sync:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check .

fix:
	uv run ruff check --fix .

# Opt-in: not part of `lint`, so formatting is never enforced in CI or on port.
format:
	uv run ruff format .

clean:
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache
	find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true

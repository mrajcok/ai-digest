.PHONY: sync test lint lint-sh fix format clean

sync:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check .

# Shell scripts only (run_tests.sh). Kept out of `lint` so that target stays
# exactly `ruff check .` and doesn't need shellcheck installed.
lint-sh:
	git ls-files --cached --others --exclude-standard '*.sh' | xargs -r shellcheck

fix:
	uv run ruff check --fix .

# Opt-in: not part of `lint`, so formatting is never enforced in CI or on port.
format:
	uv run ruff format .

clean:
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache
	find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true

# CI-lite targets — the uniform green-tree gate for every implementation unit.
# `make check` is the gate the orchestrator runs before committing a unit.
.PHONY: check test lint

check: test lint

test:
	pytest

# ruff is optional (see pyproject [project.optional-dependencies].dev).
# Lint stays a soft gate on *absence* — a machine without dev extras skips it and
# `make check` is still green. But when ruff IS installed, both its checks are
# HARD: `format --check` was previously not run at all, which is how the tree
# drifted to 65-of-88 files unformatted while the gate reported green.
lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check . && ruff format --check .; \
	else \
		echo "ruff not installed — skipping lint (pip install -e '.[dev]' to enable)"; \
	fi

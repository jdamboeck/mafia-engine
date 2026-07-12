# CI-lite targets — the uniform green-tree gate for every implementation unit.
# `make check` is the gate the orchestrator runs before committing a unit.
.PHONY: check test lint

check: test lint

test:
	pytest

# ruff is optional (see pyproject [project.optional-dependencies].dev).
# Lint is a soft gate: run ruff if installed, otherwise skip without failing
# so `make check` stays green on a machine that has not installed dev extras.
lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check .; \
	else \
		echo "ruff not installed — skipping lint (pip install -e '.[dev]' to enable)"; \
	fi

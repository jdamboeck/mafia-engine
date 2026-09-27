# CI-lite targets — the uniform green-tree gate for every implementation unit.
# `make check` is the gate the orchestrator runs before committing a unit.
.PHONY: check test lint typecheck

check: test lint typecheck

test:
	pytest

# ruff is optional (see pyproject [project.optional-dependencies].dev).
# Lint stays a soft gate on *absence* — a machine without ruff skips it (though
# `make check` as a whole still needs the dev extras, for pyright below). But when
# ruff IS installed, both its checks are
# HARD: `format --check` was previously not run at all, which is how the tree
# drifted to 65-of-88 files unformatted while the gate reported green.
lint:
	@if python -m ruff --version >/dev/null 2>&1; then \
		python -m ruff check . && python -m ruff format --check .; \
	else \
		echo "ruff not installed — skipping lint (pip install -e '.[dev]' to enable)"; \
	fi

# pyright is a HARD gate, including on absence (unlike ruff above). It is pinned in
# the dev extra (pyproject [project.optional-dependencies].dev) and configured in
# [tool.pyright] (standard mode; engine, clients, data, tests), so a missing pyright
# means the dev extras are not installed, and that fails the gate rather than
# skipping it. `pip install -e '.[dev]'` provides it; the first run downloads the
# pyright npm package, so it needs network once.
typecheck:
	python -m pyright

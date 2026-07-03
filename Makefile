# Local dev shortcuts. Equivalent to the run.ps1 + CI commands.
# Use `make help` to list.

.PHONY: help install lint format test typecheck run precommit clean

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install package + dev deps.
	python -m pip install -U pip
	pip install -r requirements-dev.txt
	pip install -e .

test:  ## Run the test suite.
	pytest -v

lint:  ## Run ruff (check only).
	ruff check src tests

format:  ## Run ruff --fix + format.
	ruff check --fix src tests
	ruff format src tests

typecheck:  ## Run mypy.
	mypy src/gitlab_issues_finder

precommit:  ## Run pre-commit on all files.
	pre-commit run --all-files

run:  ## Run the web app (foreground).
	python -m gitlab_issues_finder

clean:  ## Remove build / cache artifacts.
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

.PHONY: help setup test test-ha format format-check lint typecheck check hassfest hacs kpi

PYTHON ?= .venv/bin/python
HA_PYTHON ?= .venv-ha/bin/python
BASE_PYTHON ?= python3.14

test:
	$(PYTHON) -m coverage run --branch --source=custom_components/osservaprezzi_carburanti -m pytest -q
	$(PYTHON) -m coverage report --fail-under=100 --show-missing

help:
	@printf '%s\n' \
		'setup          Create test environments and install dependencies' \
		'check          Run lint, type checking, and both test suites' \
		'lint           Check Python code style and formatting' \
		'format         Format Python code' \
		'format-check   Check Python formatting' \
		'typecheck      Run type checking against Home Assistant' \
		'test           Run unit tests with coverage' \
		'test-ha        Run Home Assistant contract tests' \
		'hassfest       Run Home Assistant integration validation' \
		'hacs           Validate the remote GitHub ref with HACS' \
		'kpi            Compare release performance and correctness'

setup:
	@if [ ! -x .venv/bin/python ] || [ ! -x .venv-ha/bin/python ]; then \
		command -v $(BASE_PYTHON) >/dev/null 2>&1 || { echo 'Missing $(BASE_PYTHON); install it or set BASE_PYTHON to an available Python executable.' >&2; exit 1; }; \
	fi
	@test -x .venv/bin/python || $(BASE_PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements-test.txt
	@test -x .venv-ha/bin/python || $(BASE_PYTHON) -m venv .venv-ha
	.venv-ha/bin/python -m pip install -r requirements-ha-test.txt

test-ha:
	$(HA_PYTHON) -m pytest tests/ha -o asyncio_mode=auto -q

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(HA_PYTHON) -m mypy

check: lint typecheck test test-ha

hassfest:
	docker run --rm -v "$(CURDIR):/github/workspace" ghcr.io/home-assistant/hassfest

hacs:
	@validation_token="$$(gh auth token)"; \
	repository="$$(gh repo view --json nameWithOwner --jq .nameWithOwner)"; \
	ref="$$(git branch --show-current)"; \
	echo "HACS validates the remote GitHub ref $$repository@$$ref; unpushed changes are not visible."; \
	docker run --rm -v "$(CURDIR):/github/workspace" \
		-e GITHUB_WORKSPACE=/github/workspace \
		-e INPUT_GITHUB_TOKEN="$$validation_token" \
		-e INPUT_CATEGORY=integration \
		-e INPUT_REPOSITORY="$$repository" \
		-e INPUT_COMMENT=false \
		-e REPOSITORY_REF="$$ref" \
		ghcr.io/hacs/action:main

# Resolve the preceding stable release by default; override KPI_BASELINE when needed.
KPI_BASELINE ?= previous-stable
KPI_ARGS ?=
kpi:
	$(PYTHON) scripts/benchmark_release.py --baseline $(KPI_BASELINE) $(KPI_ARGS)

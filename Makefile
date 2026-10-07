.PHONY: test test-ha lint typecheck check hassfest hacs

PYTHON ?= .venv/bin/python
HA_PYTHON ?= .venv-ha/bin/python

test:
	$(PYTHON) -m coverage run --source=custom_components/osservaprezzi_carburanti -m pytest -q
	$(PYTHON) -m coverage report --fail-under=100

test-ha:
	$(HA_PYTHON) -m pytest -c pytest-ha.ini -q

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy

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

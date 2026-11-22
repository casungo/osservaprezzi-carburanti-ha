# Testing and Validation

All routine checks are wired into the repository `Makefile` (`make help` lists every target) and
run automatically in GitHub Actions on every push and pull request.

## Local setup

The repository uses two virtual environments:

- `.venv` (Python 3.11+): unit tests, coverage, Ruff lint/format.
- `.venv-ha` (Python 3.14): `mypy` against real Home Assistant types and `tests/ha/` contract
  tests (including static JSON/YAML validation).

Bootstrap both environments in one step:

```bash
make setup
```

Then run the full local validation suite:

```bash
make check
```

Individual targets: `make lint`, `make format`, `make format-check`, `make typecheck`,
`make test`, and `make test-ha`. Override `PYTHON` or `HA_PYTHON` when using custom environments.

If you use [`pre-commit`](https://pre-commit.com/), enable the hooks in `.pre-commit-config.yaml`
with `pre-commit install`.

## Test lanes

### Lightweight unit tests (`tests/unit/`)

Fast unit tests with hand-rolled Home Assistant mocks in `tests/unit/conftest.py` (they do not
import Home Assistant). When adding a new `homeassistant.*` import in the integration, expose the
matching module or attribute in `tests/unit/conftest.py` as well.

```bash
make test
```

Enforces **100% statement and branch coverage** across `custom_components/osservaprezzi_carburanti`
(configured in `pyproject.toml`), including architecture invariants
(`tests/unit/test_architecture_invariants.py`) and translation/service parity
(`tests/unit/test_translations_parity.py`).

### Real Home Assistant contract tests (`tests/ha/`)

Runs inside a real Home Assistant test harness via `pytest-homeassistant-custom-component`,
including repository JSON/YAML static validation (`tests/ha/test_static_files.py`):

```bash
make test-ha
```

### Live MIMIT API contract (optional, network required)

Gated behind an environment variable so it never runs by accident:

```bash
OSSERVAPREZZI_LIVE_API=1 .venv/bin/python -m pytest tests/unit/test_live_api_contract.py -q
```

## Docker validators and smoke regression

With Docker available locally:

```bash
make hassfest
make hacs                                       # validates the pushed GitHub branch
python3 scripts/ha_docker_regression.py --timeout 240
```

`ha_docker_regression.py` runs `fresh`, `lived` (4 boots + 14 synthetic days of Recorder history),
`upgrade` (from the previous baseline), `outage`, and `recovery` profiles against the official
Home Assistant container without touching any live instance.

For pre-release guardrails and KPI comparisons (`make kpi`), see
[release-validation.md](./release-validation.md).

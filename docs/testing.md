# Testing and Validation

This project ships two test lanes, optional validators, and a Docker smoke regression. The GitHub
Actions workflow runs the same commands on every push and pull request.

## Test lanes

### Lightweight unit tests (default lane)

Fast unit tests with hand-rolled mocks. They do not import Home Assistant.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-test.txt

python -m pytest -q
```

CI enforces 100% coverage of the integration package:

```bash
python -m coverage run --source=custom_components/osservaprezzi_carburanti -m pytest -q
python -m coverage report --fail-under=100
```

### Real Home Assistant contract tests

Contract tests that run inside a real Home Assistant test harness via
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component).
They live in `tests_ha/` and are excluded from the default lane by `tests_ha/conftest.py` unless
the `pytest-ha.ini` config is loaded.

```bash
python -m pip install -r requirements-ha-test.txt
python -m pytest -c pytest-ha.ini -q
```

`requirements-ha-test.txt` pulls in the default lane dependencies plus `cronsim` and a pinned
version of `pytest-homeassistant-custom-component`.

### Live API contract test (optional, network required)

A small contract test against the real MIMIT/Osservaprezzi endpoints. It is gated behind an
environment variable so it never runs by accident:

```bash
OSSERVAPREZZI_LIVE_API=1 python -m pytest tests/test_live_api_contract.py -q
```

## Validators

`hassfest` and HACS validation run in CI. Locally, the `Makefile` wraps both in Docker:

```bash
make hassfest
make hacs
```

The `hacs` target validates the current branch as pushed to GitHub, so unpushed changes are not
visible to it. If the tools are installed locally, the direct equivalents are:

```bash
hassfest --action validate --path .
hacs validate integration custom_components/osservaprezzi_carburanti
```

## Docker smoke regression

With Docker running locally, you can run a Home Assistant smoke regression against the official
Home Assistant container:

```bash
python scripts/ha_docker_regression.py --timeout 240
```

The same regression runs in GitHub Actions on the 1st and 15th of every month and is available
through manual workflow dispatch. Pre-release validation steps are described in
[release-validation.md](./release-validation.md).

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

## Docker state regression

With Docker running locally, you can run a two-profile regression against the official Home
Assistant container:

```bash
python scripts/ha_docker_regression.py --timeout 240
```

The runner first starts a separate builder profile four times in the official Home Assistant
container. On its first boot, the builder creates stations through the real config-flow manager.
On every boot it writes 14 days of synthetic state changes through Home Assistant's real state
machine. Recorder commits those changes to the real SQLite database. After the last shutdown, the
runner checkpoints SQLite, exports the complete profile to a tar archive, and extracts it into a
new `lived` profile.

`fresh` is a separate empty profile. `lived` starts from the exported builder profile, with config
entries, entity registry, station cache, Recorder database, and four previous HA boots already
present. The dates are synthetic by design; the Docker boots, state writes, Recorder persistence,
export, and restore are real.

The runner also builds the same aged profile with the previous tagged integration (`v2.6.0-beta.1`),
replaces only its integration code with the current checkout, and runs the probe as `upgrade`. This
keeps upgrade compatibility separate from the normal `fresh` and `lived` checks.

Finally, it runs the exported lived profile twice with only the MIMIT hostnames mapped to localhost:
`outage` checks that cached registry data and the last successful station payload keep entities
available, while `recovery` restores the network on the same profile and verifies real refresh and
service calls.

Inside Home Assistant, the test-only probe checks the four setup paths, multi-station selection,
custom radius and result limits, duplicate skipping, real entity states, service responses, stable
entity IDs after reload, and persisted entries. The station and registry requests are real MIMIT
requests. The probe writes only its result file inside the temporary profile.

The runner also checks container startup logs, imports the integration against the container's HA
runtime, verifies entity-registry counts, and checks nearby discovery. It does not alter the user's
Home Assistant instance. The same regression runs in GitHub Actions on pushes, pull requests, the
1st and 15th of every month, and manual workflow dispatch. Pre-release validation steps are described in
[release-validation.md](./release-validation.md).

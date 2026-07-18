# Release validation

Run these checks before publishing a HACS/Home Assistant release.

## Local environment

On a host that provides `python3` but not `python`, create and activate a virtual environment first:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-test.txt
```

## Required for every PR

```bash
python -m pytest -q
python -m coverage run --source=custom_components/osservaprezzi_carburanti -m pytest -q
python -m coverage report --fail-under=100
python -m ruff check .
python -m mypy
make hassfest
make hacs
```

Both commands use the official validator containers, so no global hassfest or HACS installation is
required. Hassfest reads the local checkout. The HACS action validates the current branch on GitHub;
push the branch first when validating unpublished changes.

## Real Home Assistant smoke

Run the Docker regression when Docker is available:

```bash
python scripts/ha_docker_regression.py
```

This starts a real Home Assistant container, copies the custom integration, creates live config
entries for known station IDs, checks module imports inside the HA runtime, waits for entities,
and fails on integration startup tracebacks.

GitHub Actions runs this regression on the 1st and 15th of each month. It can also be started with
manual workflow dispatch; the local command remains available for pre-release checks.

## Live upstream contract

Run only on demand or nightly, not as a required PR check:

```bash
OSSERVAPREZZI_LIVE_API=1 python -m pytest tests/test_live_api_contract.py -q
```

Use `OSSERVAPREZZI_LIVE_STATION_ID=<id>` to override the known station.

## Manual canary

- Install the integration into a clean Home Assistant profile.
- Upgrade from the previous released version without clearing `.storage`.
- Configure a known station through the config flow.
- Verify fuel sensors, station info sensors, location attributes, opening-hours entities, and service entities.
- Reload the config entry and confirm cron scheduling is recreated once.
- Unload the config entry and confirm listeners/services are cleaned up.
- Run `force_csv_update`, `clear_cache`, and `compare_stations` from Developer Tools; capture the
  response from `compare_stations` and verify that it contains every loaded station with current fuel
  data.
- Confirm no blocking-call or thread-safety warnings are logged with Home Assistant debug mode enabled.

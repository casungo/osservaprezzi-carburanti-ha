# Release validation

Run these checks before publishing a HACS/Home Assistant release.

## Release guard

Run the guard after committing the release version and changelog, before creating or pushing a tag.
It reads `manifest.json` from the exact target commit and checks the release naming, tag, and
remote tag state. It also rejects a dirty worktree and requires the full target SHA to equal the
live head of the explicitly named remote branch. The remote must be reachable.

For a stable release:

```bash
VERSION=2.6.0
TAG="v${VERSION}"
BRANCH=master
TARGET_COMMIT="$(git rev-parse HEAD)"
python scripts/release_guard.py --channel stable --version "$VERSION" --tag "$TAG" --commit "$TARGET_COMMIT" --branch "$BRANCH"
git tag -a "$TAG" "$TARGET_COMMIT" -m "Release $VERSION"
git push origin "$TAG"
gh release create "$TAG" --target "$TARGET_COMMIT" --title "$TAG"
```

For a prerelease, include a valid SemVer suffix. A plain `X.Y.Z` is never accepted as a
prerelease:

```bash
VERSION=2.6.0-beta.1
TAG="v${VERSION}"
BRANCH=release/2.6.0-beta.1
TARGET_COMMIT="$(git rev-parse HEAD)"
python scripts/release_guard.py --channel prerelease --version "$VERSION" --tag "$TAG" --commit "$TARGET_COMMIT" --branch "$BRANCH"
git tag -a "$TAG" "$TARGET_COMMIT" -m "Release $VERSION"
git push origin "$TAG"
gh release create "$TAG" --target "$TARGET_COMMIT" --title "$TAG" --prerelease
```

If the guard fails, do not create, move, or force-push the tag. It accepts stable versions in the
form `X.Y.Z` and prereleases with a valid SemVer suffix, such as `X.Y.Z-beta.N` or `X.Y.Z-rc.N`.
Push the named branch first, and do not change it between the guard and tag push.

## Local environment

On a host that provides `python3` but not `python`, create and activate a virtual environment first:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-ha-test.txt
```

`requirements-ha-test.txt` includes the lightweight suite dependencies and the
Home Assistant contract-test dependencies used by CI.

## Required for every PR

```bash
python -m pytest -c pytest-ha.ini -q
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

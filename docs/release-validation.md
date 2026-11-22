# Release Validation

Run these checks before tagging a HACS/Home Assistant release. For day-to-day testing and local
setup, see [testing.md](./testing.md).

## 1. Required validation checks

```bash
make check
make hassfest
make hacs
.venv/bin/python scripts/ha_docker_regression.py
```

Optional live upstream contract check:

```bash
OSSERVAPREZZI_LIVE_API=1 .venv/bin/python -m pytest tests/unit/test_live_api_contract.py -q
```

## 2. KPI comparisons (`make kpi`)

Compare the current checkout against the preceding stable tag inside identical isolated Home
Assistant containers using one captured, hash-verified MIMIT snapshot:

```bash
make kpi
# Or compare against an explicit baseline:
make kpi KPI_BASELINE=v2.6.0
```

- Output is written under `artifacts/kpi/<timestamp>/` (`report.json`, `report.md`, logs, inputs).
- Correctness failures (price mismatches, entity ID drift, listener/timer/task leaks after unload,
  missing conditional `304`) fail with exit code `1`.
- Performance metrics are flagged provisionally when an increase exceeds **15%**, the metric's
  absolute floor (`METRICS` in `scripts/benchmark_release.py`), and observed baseline noise. Pass
  `KPI_ARGS='--fail-on-performance'` to return exit code `2` on flags.
- Replay benchmarks do not measure live upstream latency, multi-day memory slope, or production
  uptime.

## 3. Release guard and tagging

Run `scripts/release_guard.py` after committing the version bump (`manifest.json`) and
`CHANGELOG.md`, and after pushing the target branch, before creating a tag. It requires a clean
worktree and checks that `HEAD` matches the remote branch head and tag/channel naming rules
(stable `X.Y.Z`; prerelease requires a SemVer suffix such as `X.Y.Z-beta.N`).

Stable release:

```bash
VERSION=2.8.0
TAG="v${VERSION}"
BRANCH=master
TARGET_COMMIT="$(git rev-parse HEAD)"
.venv/bin/python scripts/release_guard.py --channel stable --version "$VERSION" --tag "$TAG" --commit "$TARGET_COMMIT" --branch "$BRANCH"
git tag -a "$TAG" "$TARGET_COMMIT" -m "Release $VERSION"
git push origin "$TAG"
gh release create "$TAG" --target "$TARGET_COMMIT" --title "$TAG"
```

Prerelease:

```bash
VERSION=2.8.0-beta.4
TAG="v${VERSION}"
BRANCH=master
TARGET_COMMIT="$(git rev-parse HEAD)"
.venv/bin/python scripts/release_guard.py --channel prerelease --version "$VERSION" --tag "$TAG" --commit "$TARGET_COMMIT" --branch "$BRANCH"
git tag -a "$TAG" "$TARGET_COMMIT" -m "Release $VERSION"
git push origin "$TAG"
gh release create "$TAG" --target "$TARGET_COMMIT" --title "$TAG" --prerelease
```

## 4. Manual canary checklist

- Upgrade an existing Home Assistant instance from the previous release without clearing `.storage`.
- Configure a station through the config flow and verify fuel, info, location, opening-hours,
  service, and refresh-button entities.
- Reload and unload the entry and confirm no blocking-call, thread-safety, or residual-listener
  warnings appear with debug logging enabled.
- Run `compare_stations`, `search_registry`, `refresh_prices`, `force_csv_update`, and
  `clear_cache` from Developer Tools.

CI runs KPI comparisons on pull requests that change the integration or KPI scripts, or
when labeled `run-kpi`. Pull requests use one round, five repeats, three reloads and a
15-second idle period; the `full-kpi` label selects the full settings. Master pushes use
the full settings. Docker regression uses two profile workers by default; see
[testing.md](./testing.md) for sequential execution.

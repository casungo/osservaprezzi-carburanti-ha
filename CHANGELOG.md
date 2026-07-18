# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.3.0] - 2026-07-19

### Added
- Discover newly available station, opening-hours, and service entities after coordinator refreshes without requiring an integration reload
- Add lifecycle contract tests against Home Assistant and regression coverage for reloads, dynamic entity discovery, shared cache behavior, and service responses
- Add design documents for nearby-station discovery and ranked station comparisons

### Changed
- Share one station registry and cache state across all configured entries instead of loading and maintaining a separate copy for every station
- Validate station API payloads before publishing coordinator data and reduce verbose logging of raw upstream responses
- Write cache metadata atomically and stop retaining the redundant raw station record in coordinator data
- Pin GitHub Actions dependencies, add Dependabot updates, run the live upstream contract check daily, and run the Home Assistant Docker regression twice monthly
- Expand the English and Italian documentation for global services, local validation, and release checks

### Fixed
- Prevent an unloaded config entry from rearming its scheduled refresh while an update is still in progress
- Preserve previous fuel prices and price-change timestamps across refreshes when prices do not change
- Reject malformed or truncated CSV downloads without replacing the last valid station registry
- Report cache update and clear failures as Home Assistant service errors instead of returning error fields
- Handle opening-hours ranges that cross midnight and calculate their next state change correctly
- Keep the Docker regression import check aligned with the current entity helper module

### Removed
- Remove superseded scheduling helpers and the redundant raw station cache

## [2.2.0] - 2026-06-08

### Added
- Add Battery State Card dashboard examples for displaying and grouping fuel prices
- Add a Docker-based smoke regression against a real Home Assistant runtime and a documented release-validation checklist
- Add broader regression coverage for API, config flow, coordinator, CSV cache, entity, and live upstream behavior

### Changed
- Move opening-hours and station-service entities to dedicated shared entity and binary-sensor implementations with translated diagnostic names
- Strengthen shared CSV cache update and clear operations across all configured station entries
- Refresh time-sensitive opening-hours entity state independently of price polling

### Removed
- Remove legacy service sensor registry entries superseded by service binary sensors

## [2.1.6] - 2026-04-30

### Fixed
- Keep opening-hours entities fresh during the day instead of only updating them on price refresh
- Normalize service payload handling so binary service sensors are created for mixed API formats
- Compute H24 opening-hours next changes from the current schedule instead of any H24 day in the payload
- Parse quoted CSV fields correctly when a station field contains the detected separator

### Changed
- Refactor sensor entities around shared station helpers and translated diagnostic entity names
- Tighten exception handling in config flow, cron helpers, setup scheduling, and CSV cache management
- Remove unused geolocation platform code, unused translation keys, and debug-only CSV helper methods
- Deduplicate service coordinator iteration during service handling

## [2.1.5] - 2026-03-22

### Added
- HA services: `force_csv_update` and `clear_cache` for manual CSV data management
- `geo_location` platform: stations appear on the HA map panel
- Price change detection: `previous_price` and `price_changed_at` attributes on fuel sensors
- Multi-station comparison service: `compare_stations` returns price data for all configured stations
- Retry with exponential backoff (3 retries: 30s, 60s, 120s) for transient API failures

### Changed
- Improved error resilience in API and coordinator

## [2.1.4] - 2025-01-18

### Fixed
- Fix indentation in CSV cache loader
- Fix resilient dict parsing and KeyError avoidance in service sensors
- Fix TypeError when subtracting naive datetimes loaded from storage

### Changed
- Replace aiofiles with `asyncio.to_thread` file I/O (remove external dependency)
- Remove cronsim requirement from manifest (soft dependency)
- Require cronsim and use HA dt_util for timezone handling
- Normalize git line endings for translation mappings
- Extract HTTP communication and error handling to `api.py`

## [2.1.3] - 2025-01-12

### Added
- Migrate CSV and cache files to Home Assistant `.storage` directory with legacy file migration support

### Changed
- Auto-detect CSV separator (pipe `|` or semicolon `;`) and bump cache version to 2.0

## [2.1.0] - 2024-12-XX

### Added
- Map integration with station location sensor and GPS coordinates
- Flexible cron-based update scheduling via options flow

### Changed
- Remove zone search functionality and hardcoded fuel type mapping
- Remove FUEL_TYPES mapping; use dynamic fuel names from API

## [2.0.0] - 2024-11-XX

### Added
- Station opening hours sensors (open/closed status, next schedule change)
- Binary sensors for station additional services (food, workshop, Wi-Fi, EV charging, etc.)
- Italian holiday detection for opening hours

### Changed
- Refactor integration to `osservaprezzi_carburanti` domain
- Refactor update scheduling and options flow

## [1.0.0] - 2024-10-XX

### Added
- Initial release
- Fuel price sensors for Italian gas stations via MISE Osservaprezzi API
- Station information sensors (name, address, brand, contact info)
- Config flow with station ID input
- HACS integration support
- Italian and English translations

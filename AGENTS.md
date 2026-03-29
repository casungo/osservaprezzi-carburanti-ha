# AGENTS.md

Guide for agentic coding agents working in this repository.

## Project Overview

This is a **Home Assistant custom integration** (HACS) called "Osservaprezzi Carburanti" that retrieves Italian fuel prices from the MISE/Osservaprezzi government API. It is distributed via HACS and runs inside the Home Assistant ecosystem.

- **Domain**: `osservaprezzi_carburanti`
- **Integration type**: `service` (cloud_polling)
- **Python version**: 3.11+ (uses `from __future__ import annotations` and `X | None` union syntax)
- **Source root**: `custom_components/osservaprezzi_carburanti/`

## Repository Structure

```
custom_components/osservaprezzi_carburanti/
  __init__.py          # Entry point: setup/unload/reload config entries, cron scheduling
  api.py               # Async API helper: fetches station data from MISE REST API
  config_flow.py       # Config & options flow (station ID input, cron expression)
  const.py             # Constants: domain, config keys, API URLs, service maps, headers
  coordinator.py       # DataUpdateCoordinator: orchestrates data fetch + CSV enrichment
  cron_helper.py       # Cron expression validation and next-run calculation (cronsim)
  csv_manager.py       # Downloads/parses/caches a large CSV of all Italian stations
  manifest.json        # HACS/HA manifest (domain, version, codeowners, etc.)
  sensor.py            # Sensor + BinarySensor entities (fuel prices, info, hours, services)
  translations/
    en.json            # English UI strings
    it.json            # Italian UI strings
.github/workflows/
  main.yml             # CI: HACS validation + hassfest
```

## Build / Lint / Test Commands

### Validation (CI)

```bash
# These run in GitHub Actions on every push/PR:

# 1. HACS validation (checks repository structure, manifest, etc.)
#    Uses: hacs/action@22.5.0 with category "integration"

# 2. Hassfest (Home Assistant's integration linter/validator)
#    Uses: home-assistant/actions/hassfest@master
```

### Local validation

There is no local test suite, no `pyproject.toml`, no `pytest.ini`, and no `requirements.txt` content. To validate locally:

```bash
# Install hassfest locally (optional, for pre-push validation)
pip install hassfest
hassfest --action validate --path .

# Install hacs/validation locally (optional)
pip install hacs
hacs validate integration custom_components/osservaprezzi_carburanti
```

### Running tests

There are **no tests** in this repository. If adding tests, use **pytest** with **pytest-asyncio** and the `homeassistant` test helpers (`pytest-homeassistant-custom-component`). Place tests in a `tests/` directory at the repo root.

To run a single test file:
```bash
pytest tests/test_sensor.py
```

To run a single test function:
```bash
pytest tests/test_sensor.py::test_function_name -v
```

## Code Style Guidelines

### Imports

Order: **stdlib → third-party → homeassistant → local (relative)**, separated by blank lines. Every file starts with:

```python
from __future__ import annotations
```

Example import block (from `sensor.py`):
```python
from __future__ import annotations
import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_STATION_ID,
    ...
)
from .coordinator import CarburantiDataUpdateCoordinator
```

### Type Annotations

- Use modern union syntax: `str | None` (not `Optional[str]`), `dict[str, Any]` (not `Dict[str, Any]`)
- Some older-style `Optional` and `Dict` imports exist in `csv_manager.py`; prefer modern syntax for new code
- Always annotate function parameters and return types
- Use `from __future__ import annotations` in every file to enable forward references

### Naming Conventions

- **Constants**: `UPPER_SNAKE_CASE` in `const.py` (e.g., `DOMAIN`, `CONF_STATION_ID`, `BASE_URL`)
- **Functions/Methods**: `snake_case` (e.g., `async_setup_entry`, `_parse_time`)
- **Classes**: `PascalCase` (e.g., `CarburantiDataUpdateCoordinator`, `StationInfoSensor`)
- **Private methods**: Prefix with `_` (e.g., `_async_update_data`, `_is_currently_open`)
- **Module-level logger**: `_LOGGER = logging.getLogger(__name__)`
- **Config entry IDs**: `entry.entry_id`, `entry.data[CONF_STATION_ID]`

### Logging

- Always use `_LOGGER = logging.getLogger(__name__)` at module level
- Use `_LOGGER.debug()` for detailed flow, `_LOGGER.info()` for key events, `_LOGGER.warning()` for recoverable issues, `_LOGGER.error()` for failures
- Use `%s` style string formatting in log calls (not f-strings): `_LOGGER.info("Setting up %s", entry.title)`

### Error Handling

- API errors: catch specific `aiohttp.ClientResponseError` and `aiohttp.ClientError`, re-raise as `UpdateFailed` in the coordinator
- Config flow: define custom exceptions extending `HomeAssistantError` (e.g., `CannotConnect`, `InvalidStation`)
- Use `try/except` around parsing code with specific exception types; log and return safe defaults
- Never silently swallow exceptions without logging

### Async Patterns

- All HA-facing functions are `async def`
- Use `async_get_clientsession(hass)` for HTTP requests (never create raw `aiohttp.ClientSession`)
- Use `hass.async_add_executor_job()` or `asyncio.to_thread()` to offload blocking I/O (file ops, heavy CSV parsing)
- Coordinator pattern: extend `DataUpdateCoordinator`, implement `_async_update_data()`
- Sensor entities: extend `CoordinatorEntity` + `SensorEntity` or `BinarySensorEntity`

### Entity Patterns

- Use `_attr_*` class attributes for static entity properties (e.g., `_attr_entity_category`, `_attr_icon`, `_attr_state_class`)
- Use `_attr_translation_key` for translatable entity names; add keys to `translations/en.json` and `translations/it.json`
- Use `_attr_unique_id` for stable entity IDs: `f"{station_id}_{descriptor}"`
- `DeviceInfo` must use `identifiers={(DOMAIN, station_id)}` for all entities of the same station
- `device_info` should be a `@property` returning `DeviceInfo` with name, manufacturer, model
- All sensor platforms use `async_setup_entry(hass, entry, async_add_entities)` as the entry point

### Comments

- Comments in this codebase are in English
- Use `"""docstrings"""` for classes and public functions
- Inline comments are sparse; use them only for non-obvious logic
- Do NOT add comments unless asked

### Key Conventions

- **Cron scheduling**: uses `cronsim` library (soft dependency, gracefully degrades if missing)
- **CSV data**: downloaded from MIMIT government site, parsed with auto-detected separator (`|` or `;`), cached in `.storage/` as JSON
- **Config entry version**: currently `2` (see `VERSION` in config_flow and `async_migrate_entry`)
- **Platform**: only `SENSOR` platform is registered
- **Update mechanism**: cron-based rescheduling via `async_track_time_interval`, not `update_interval` on the coordinator
- **Fuel key format**: `"{fuel_name}_{service_type}"` where service_type is `"self"` or `"servito"`

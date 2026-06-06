# AGENTS.md

Guide for agentic coding agents working in this repository.

## Project Overview

This repository contains a Home Assistant custom integration distributed through HACS. The integration retrieves Italian fuel station data and prices from the official MIMIT/Osservaprezzi sources, enriches station data with locally cached CSV metadata, and exposes the result through Home Assistant entities and services.

Stable project facts:

- Domain: `osservaprezzi_carburanti`
- Integration type: Home Assistant `service`
- IoT class: `cloud_polling`
- Python: 3.11+
- Runtime style: async Home Assistant integration
- Distribution target: HACS and Home Assistant custom components

Avoid assuming that a specific module or platform file exists. Inspect the current tree before referencing or editing concrete files.

## Main Features

The integration generally supports:

- Config flow setup using a station ID.
- Options flow for refresh scheduling.
- Cron-based refresh scheduling.
- Fuel price sensors.
- Station information entities.
- Opening-hours derived entities.
- Additional station service entities when the upstream API provides service metadata.
- CSV download, parsing, and cache management for station registry data.
- Global Home Assistant services for cache/update/compare workflows.

Feature names and exact entity/platform layout may evolve. Always confirm against the current repository before making changes.

## Validation Commands

Run the local pytest suite after code changes:

```bash
python -m pytest -q
```

To run a focused test file:

```bash
python -m pytest tests/test_sensor.py -q
```

To run a focused test function:

```bash
python -m pytest tests/test_sensor.py::test_function_name -v
```

Optional pre-release validation, when the tools are installed:

```bash
hassfest --action validate --path .
hacs validate integration custom_components/osservaprezzi_carburanti
```

Do not assume optional validators are installed locally. If they are unavailable, report that and still run the pytest suite.

## Code Style

Use the repository's existing style. For new Python code:

- Start modules with `from __future__ import annotations`.
- Order imports as stdlib, third-party, Home Assistant, then local imports, separated by blank lines.
- Use modern type syntax such as `str | None` and `dict[str, Any]`.
- Annotate function parameters and return types.
- Use `snake_case` for functions and methods.
- Use `PascalCase` for classes.
- Use `UPPER_SNAKE_CASE` for constants.
- Keep module loggers as `_LOGGER = logging.getLogger(__name__)`.
- Use `%s` formatting in log calls instead of f-strings.
- Keep comments sparse and in English.
- Add docstrings for public classes and public functions.

## Home Assistant Patterns

Follow Home Assistant async rules carefully:

- Home Assistant setup, unload, reload, service handlers, config flows, and update callbacks should be async where appropriate.
- Use `async_get_clientsession(hass)` for HTTP requests.
- Do not create raw `aiohttp.ClientSession` objects.
- Offload blocking file I/O and heavy parsing with `hass.async_add_executor_job()` or `asyncio.to_thread()`.
- Do not mutate Home Assistant state from worker threads.
- Do not call non-thread-safe `async_*` Home Assistant APIs from sync callbacks that may run outside the event loop.
- For entity state writes from sync callbacks, use thread-safe scheduling methods unless the callback is guaranteed to run in the event loop.
- Store unsubscribe callbacks and clean them up on unload.
- Register global services once and remove them when the last config entry unloads.

Home Assistant detects many thread-safety violations since 2024.5.0. During development, enable asyncio debug mode and Home Assistant debug mode when chasing async issues.

General rule: `async_*` APIs are event-loop-only. If code can run in an executor thread, use the sync equivalent or schedule work back onto the event loop:

- `hass.async_create_task` -> `hass.create_task`
- `hass.bus.async_fire` -> `hass.bus.fire`
- `hass.services.async_register` -> `hass.services.register`
- `hass.services.async_remove` -> `hass.services.remove`
- `async_write_ha_state` -> `self.schedule_update_ha_state()`
- `async_dispatcher_send` -> `dispatcher_send`
- registry `async_*` APIs without sync equivalents -> `hass.add_job(...)`
- `hass.config_entries.async_update_entry` from a thread -> `hass.add_job(...)`
- `issue_registry.async_get_or_create` -> `issue_registry.create_issue`
- `issue_registry.async_delete` -> `issue_registry.delete_issue`
- `issue_registry.async_ignore` from a thread -> `hass.add_job(...)`

If a sync callback registered with an HA event helper calls event-loop-only APIs, decorate it with `@callback` so Home Assistant does not run it in the executor. If it is not guaranteed to run in the event loop, use the thread-safe alternative instead.

## Coordinator And Entity Patterns

Use the DataUpdateCoordinator pattern for shared data refreshes. Entity classes should read from coordinator data and avoid doing network or file work directly.

Coordinator conventions:

- Use `async_config_entry_first_refresh()` during setup so initial failures are handled by Home Assistant.
- Raise `ConfigEntryAuthFailed` for authentication failures so Home Assistant can start reauth.
- Raise `UpdateFailed` for generic update failures.
- Use `UpdateFailed(retry_after=seconds)` when an explicit backoff is known.
- Use `_async_setup()` for one-time coordinator initialization that should run during the first refresh.
- Use `self.async_contexts()` when fetching only data needed by enabled/listening entities.
- For push-style APIs, do not pass polling parameters such as `update_method` or `update_interval`; call `coordinator.async_set_updated_data(data)` when new data arrives.
- Be aware that `async_set_updated_data(data)` resets the next poll timer if the coordinator also polls.

Entity conventions:

- Use `_attr_*` attributes for static entity metadata.
- Use translation keys for user-facing entity names when possible.
- Set `_attr_has_entity_name = True` for new entities.
- Use stable unique IDs derived from the station ID plus a descriptor.
- Group entities for the same station under the same device identifiers.
- Keep entity properties cheap and synchronous.
- Avoid side effects in entity properties.
- Do not do network or disk I/O in entity properties; fetch data in coordinator updates or entity update methods and return cached memory values from properties.
- Device registry metadata has effect only when `unique_id` is set.
- Use `async_added_to_hass()` for restore-state or subscriptions, and `async_will_remove_from_hass()` for disconnect/unsubscribe cleanup.

Friendly-name behavior depends on device membership and entity name:

- Entity not in a device: friendly name is the entity name.
- Entity in a device with a non-empty entity name: friendly name is `device name + entity name`.
- Entity in a device with `name = None`: friendly name is the device name.

## CSV And Cache Handling

The station registry CSV is a shared resource. Treat CSV/cache operations as integration-wide state, not per-entity state.

Important rules:

- Serialize operations that read, write, clear, migrate, or parse shared CSV/cache files.
- Write downloaded data atomically through a temporary file and replace operation.
- Keep blocking file operations outside the event loop.
- Avoid mutating integration object state inside executor threads; return parsed data and assign it back on the event loop.
- Preserve cached metadata such as update timestamps and HTTP conditional headers when relevant.
- Handle malformed CSV rows defensively and log recoverable parsing issues.

## Scheduling

Refresh scheduling is cron-based and uses `cronsim`.

`cronsim` is provided by the Home Assistant Core Python package, including supported Home Assistant OS and Home Assistant Container installations. Do not add or pin `cronsim` in `manifest.json` `requirements`; custom integrations should only declare requirements that are not already required by HA Core. Import `cronsim` directly in integration code, and document a minimum Home Assistant version or add runtime checks only if the integration needs a `cronsim` feature introduced in a specific HA-supported version.

Local lightweight test environments may not have Home Assistant Core or `cronsim` installed. Tests may skip cron-specific cases in that situation, but production code should target the HA runtime where Core-managed dependencies are available.

Guidelines:

- Validate user-provided cron expressions before storing them.
- Recompute and register the next scheduled refresh after each scheduled run.
- Cancel scheduled callbacks on unload.
- Avoid duplicate scheduled callbacks for the same config entry.
- Keep scheduled callbacks small and non-blocking.

## Error Handling

Use explicit exception handling:

- Convert API failures into Home Assistant update failures where appropriate.
- Treat 404 station responses as invalid or non-recoverable.
- Treat transient network and timeout errors as retryable where existing code does so.
- Log recoverable issues with enough context to diagnose station ID, cache state, or update type.
- Do not silently swallow exceptions without logging.
- Preserve last known data when the existing coordinator behavior supports it.

## Services

Global services should operate across all configured entries without duplicating shared work unnecessarily.

Guidelines:

- Register services in the integration-level setup path, not in config-entry or platform setup, when service availability should be independent of loaded entries.
- Iterate over active coordinators from `hass.data`.
- Perform shared CSV/cache operations once, then refresh or synchronize all affected entries.
- Return structured service responses where the service contract requires it.
- Keep service handlers async.
- Avoid assumptions about the number of configured stations.
- Use `@callback` only for service handlers that are synchronous and non-blocking.
- Use `async def` service handlers for async operations.
- Never do blocking I/O directly inside a service handler.
- Register entity services under the integration domain, not the platform domain.
- Response data must be JSON-serializable dictionaries. Report service errors by raising exceptions, not by returning error fields.

For `SupportsResponse`:

- `OPTIONAL` supports action execution with optional response data.
- `ONLY` is intended for services designed to return response data rather than perform actions; this is a semantic contract, even though handlers can technically have side effects.

## Blocking Operations

Home Assistant detects many blocking calls in the event loop since 2024.7.0. Any blocking call stalls the whole Home Assistant event loop.

Avoid these in the event loop:

- `open(...)`, including the following read/write operations.
- `time.sleep(...)`; use `await asyncio.sleep(...)`.
- `urllib` and other blocking network clients.
- `glob.glob`, `glob.iglob`.
- `os.walk`, `os.listdir`, `os.scandir`, `os.stat`.
- `pathlib.Path.read_*` and `pathlib.Path.write_*`.
- SSL certificate loading operations such as `SSLContext.load_default_certs`, `load_verify_locations`, `load_cert_chain`, and `set_default_verify_paths`.
- Dynamic imports in async paths unless they follow Home Assistant's async import guidance.

Use Home Assistant helpers for async networking and SSL handling:

- `homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)`
- `homeassistant.helpers.httpx_client.get_async_client(hass)`
- `homeassistant.util.ssl` helpers where appropriate

Use `hass.async_add_executor_job(...)` for blocking code in Home Assistant integration code. In library-style code without `hass`, use `asyncio.get_running_loop().run_in_executor(...)`.

## Testing Guidance

Tests use pytest with lightweight Home Assistant mocks. Add focused tests for:

- Pure helper logic.
- Config and options flow validation.
- Retry and transient-error behavior.
- CSV parsing, cache handling, and concurrency-sensitive helpers.
- Entity state and attributes.
- Service handler behavior when multiple config entries exist.

Prefer small unit tests around the behavior being changed. Broaden coverage when touching shared async, cache, scheduler, or service logic.

## Before Finishing

Before finalizing a change:

- Run focused tests for the touched area.
- Run `python -m pytest -q`.
- Check `git diff` and make sure unrelated local changes are not reverted.
- Mention any validators that could not be run.
- Keep the final summary focused on behavior changed and tests run.

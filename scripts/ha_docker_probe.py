"""Run state and config-flow assertions inside a real Home Assistant process."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.osservaprezzi_carburanti.config_flow import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MUNICIPALITY,
    CONF_PROVINCE,
    CONF_RADIUS_KM,
    CONF_RESULT_LIMIT,
)
from custom_components.osservaprezzi_carburanti.const import (
    CONF_STATION_ID,
    DOMAIN,
    SERVICE_COMPARE_STATIONS,
    SERVICE_REFRESH_PRICES,
    SERVICE_SEARCH_REGISTRY,
)

_LOGGER = logging.getLogger(__name__)
PROBE_DOMAIN = "ha_docker_probe"
ROME = {CONF_LATITUDE: 41.9028, CONF_LONGITUDE: 12.4964}
LIVED_HISTORY_DAYS = 14


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Start the probe after Home Assistant has loaded all integrations."""
    settings = config.get(PROBE_DOMAIN, {})
    hass.async_create_task(_run_probe(hass, settings), name="ha_docker_state_probe")
    return True


async def _run_probe(hass: HomeAssistant, settings: dict[str, Any]) -> None:
    """Run one fresh or persisted-profile probe and write its result."""
    profile = str(settings.get("profile", "unknown"))
    station_ids = [str(value) for value in settings.get("station_ids", [])]
    try:
        await _wait_for_started(hass)
        if profile == "builder":
            result = await _build_lived_profile(hass, station_ids)
        else:
            result = await _exercise_profile(hass, profile, station_ids)
        await _write_result(hass, {"status": "passed", "profile": profile, **result})
    except Exception as err:
        _LOGGER.exception("Home Assistant Docker state probe failed for %s", profile)
        await _write_result(
            hass,
            {
                "status": "failed",
                "profile": profile,
                "error": f"{type(err).__name__}: {err}",
            },
        )


async def _wait_for_started(hass: HomeAssistant) -> None:
    """Wait until Home Assistant has finished its startup sequence."""
    if str(getattr(hass, "state", "")).lower().endswith("running"):
        return
    started = asyncio.Event()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _event: started.set())
    await asyncio.wait_for(started.wait(), timeout=120)


async def _exercise_profile(
    hass: HomeAssistant,
    profile: str,
    station_ids: list[str],
) -> dict[str, Any]:
    """Exercise setup flows, persisted entries, entity state, and services."""
    if profile not in {"fresh", "lived", "upgrade", "outage", "recovery"}:
        raise AssertionError(f"Unknown probe profile: {profile}")
    if not station_ids:
        raise AssertionError("The Docker probe has no station IDs")

    existing_before = _configured_station_ids(hass)
    if profile == "fresh" and existing_before:
        raise AssertionError(f"Fresh profile already has config entries: {existing_before}")
    if profile != "fresh" and not existing_before:
        raise AssertionError(f"{profile} profile did not restore any config entries")

    if profile == "outage":
        configured_ids = sorted(existing_before)
        state_summary = await _wait_for_station_states(hass, configured_ids, timeout_seconds=30)
        return {
            "configured_station_ids": configured_ids,
            "state_summary": state_summary,
            "upstream": "blocked",
        }

    if profile == "recovery":
        configured_ids = sorted(existing_before)
        before_summary = await _wait_for_station_states(
            hass, configured_ids, timeout_seconds=30
        )
        await _exercise_services(hass, configured_ids[0])
        after_summary = await _wait_for_station_states(hass, configured_ids)
        return {
            "configured_station_ids": configured_ids,
            "outage_state_summary": before_summary,
            "recovery_state_summary": after_summary,
            "upstream": "restored",
        }

    manual_id = station_ids[0]
    await _exercise_manual_path(hass, manual_id, expect_new=profile == "fresh")
    await _exercise_home_path(hass, require_multi=True)
    await _exercise_coordinates_path(hass)
    await _exercise_area_path(hass)

    configured_ids = sorted(_configured_station_ids(hass))
    if not configured_ids:
        raise AssertionError("No configured stations after exercising the flows")
    state_summary = await _wait_for_station_states(hass, configured_ids)
    await _exercise_services(hass, configured_ids[0])
    reload_summary = await _exercise_reload(hass, configured_ids[0])
    return {
        "configured_station_ids": configured_ids,
        "state_summary": state_summary,
        "reload": reload_summary,
    }


async def _build_lived_profile(hass: HomeAssistant, station_ids: list[str]) -> dict[str, Any]:
    """Create an aged profile using real HA storage and the real integration."""
    if not station_ids:
        raise AssertionError("The lived-profile builder has no station IDs")

    existing = _configured_station_ids(hass)
    if not existing:
        for station_id in station_ids:
            await _create_station(hass, station_id)
        existing = _configured_station_ids(hass)
    _assert(set(station_ids).issubset(existing), f"Builder stations missing: {existing}")

    configured_ids = sorted(existing)
    await _wait_for_station_states(hass, configured_ids)
    entity_ids = sorted(
        entity.entity_id
        for entity in er.async_get(hass).entities.values()
        if entity.platform == DOMAIN
        and any(str(entity.unique_id).startswith(f"{station_id}_") for station_id in configured_ids)
    )
    _assert(bool(entity_ids), "Builder found no integration entities")

    for day_offset in reversed(range(LIVED_HISTORY_DAYS)):
        timestamp = time.time() - day_offset * 86400
        for index, entity_id in enumerate(entity_ids):
            current = hass.states.get(entity_id)
            attributes = dict(current.attributes) if current else {}
            attributes.update({"lived_fixture": True, "lived_day": LIVED_HISTORY_DAYS - day_offset})
            hass.states.async_set(
                entity_id,
                _fixture_state(current, index, day_offset),
                attributes,
                force_update=True,
                timestamp=timestamp + index / 1000,
            )

    await hass.async_block_till_done()
    await asyncio.sleep(2)
    metadata_path = Path(hass.config.path("lived-profile-builder.json"))
    metadata = await hass.async_add_executor_job(_read_json, metadata_path)
    boot_count = int(metadata.get("boot_count", 0)) + 1
    await hass.async_add_executor_job(
        _write_json,
        metadata_path,
        {"boot_count": boot_count, "history_days": LIVED_HISTORY_DAYS},
    )
    return {
        "configured_station_ids": configured_ids,
        "boot_count": boot_count,
        "history_requested": {
            "days": LIVED_HISTORY_DAYS,
            "entity_count": len(entity_ids),
        },
    }


async def _create_station(hass: HomeAssistant, station_id: str) -> None:
    """Create one config entry through Home Assistant's real flow manager."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    _assert(result["type"] is FlowResultType.MENU, f"builder menu: {result}")
    result = await _configure(hass, result["flow_id"], {"next_step_id": "station_id"})
    _assert(result["type"] is FlowResultType.FORM, f"builder station form: {result}")
    result = await _configure(hass, result["flow_id"], {CONF_STATION_ID: station_id})
    _assert(result["type"] is FlowResultType.CREATE_ENTRY, f"builder station create: {result}")


def _fixture_state(current: Any, index: int, day_offset: int) -> str:
    """Return a valid synthetic state while retaining binary state shapes."""
    if current and current.state in {"on", "off"}:
        return "on" if (index + day_offset) % 2 else "off"
    return f"{1.40 + ((index + day_offset) % 9) / 100:.2f}"


def _read_json(path: Path) -> dict[str, Any]:
    """Read a small builder marker file."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a small builder marker file."""
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


async def _exercise_manual_path(
    hass: HomeAssistant,
    station_id: str,
    *,
    expect_new: bool,
) -> None:
    """Exercise direct station-ID setup and duplicate protection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    _assert(result["type"] is FlowResultType.MENU, f"manual menu: {result}")
    result = await _configure(hass, result["flow_id"], {"next_step_id": "station_id"})
    _assert(result["type"] is FlowResultType.FORM, f"manual form: {result}")
    result = await _configure(hass, result["flow_id"], {CONF_STATION_ID: station_id})
    if expect_new:
        _assert(result["type"] is FlowResultType.CREATE_ENTRY, f"manual create: {result}")
    else:
        _assert(
            result["type"] is FlowResultType.ABORT
            and result.get("reason") == "already_configured",
            f"manual duplicate: {result}",
        )


async def _exercise_home_path(hass: HomeAssistant, *, require_multi: bool) -> None:
    """Exercise Home discovery, custom radius/limit, multi-select, and duplicates."""
    flow_id, result = await _search(
        hass,
        "home",
        {CONF_RADIUS_KM: 20, CONF_RESULT_LIMIT: 2},
    )
    options = _selector_options(result)
    _assert(bool(options), f"home returned no candidates: {result}")
    selected = [station_id for station_id in options if station_id not in _configured_station_ids(hass)]
    creating = len(selected) >= 2
    if require_multi and creating:
        selected = selected[:2]
    else:
        selected = selected[:2] if not require_multi else options[:2]
    result = await _configure(
        hass,
        flow_id,
        {CONF_STATION_ID: selected if len(selected) > 1 else selected[0]},
    )
    if creating:
        _assert(result["type"] is FlowResultType.CREATE_ENTRY, f"home selection: {result}")
    else:
        _assert(
            result["type"] is FlowResultType.FORM
            and result.get("errors", {}).get("base") == "already_configured",
            f"home duplicate selection: {result}",
        )

    duplicate_flow_id, duplicate_result = await _search(
        hass,
        "home",
        {CONF_RADIUS_KM: 20, CONF_RESULT_LIMIT: 2},
    )
    duplicate_options = _selector_options(duplicate_result)
    duplicate_ids = [station_id for station_id in selected if station_id in duplicate_options]
    _assert(bool(duplicate_ids), f"home duplicate candidates disappeared: {duplicate_result}")
    duplicate = await _configure(
        hass,
        duplicate_flow_id,
        {CONF_STATION_ID: duplicate_ids},
    )
    _assert(
        duplicate["type"] is FlowResultType.FORM
        and duplicate.get("errors", {}).get("base") == "already_configured",
        f"home duplicate skip: {duplicate}",
    )


async def _exercise_coordinates_path(hass: HomeAssistant) -> None:
    """Exercise manual coordinates and a one-result custom-radius search."""
    flow_id, result = await _search(
        hass,
        "coordinates",
        {
            **ROME,
            CONF_RADIUS_KM: 200,
            CONF_RESULT_LIMIT: 1,
        },
    )
    options = _selector_options(result)
    _assert(len(options) <= 1 and bool(options), f"coordinates limit failed: {result}")
    selected = options[0]
    result = await _configure(hass, flow_id, {CONF_STATION_ID: [selected]})
    if result["type"] is FlowResultType.FORM:
        _assert(
            result.get("errors", {}).get("base") == "already_configured",
            f"coordinates duplicate: {result}",
        )
    else:
        _assert(result["type"] is FlowResultType.CREATE_ENTRY, f"coordinates: {result}")


async def _exercise_area_path(hass: HomeAssistant) -> None:
    """Exercise municipality/province discovery and its result limit."""
    flow_id, result = await _search(
        hass,
        "area",
        {
            CONF_MUNICIPALITY: "Roma",
            CONF_PROVINCE: "RM",
            CONF_RESULT_LIMIT: 1,
        },
    )
    options = _selector_options(result)
    _assert(len(options) <= 1 and bool(options), f"area limit failed: {result}")
    result = await _configure(hass, flow_id, {CONF_STATION_ID: [options[0]]})
    if result["type"] is FlowResultType.FORM:
        _assert(
            result.get("errors", {}).get("base") == "already_configured",
            f"area duplicate: {result}",
        )
    else:
        _assert(result["type"] is FlowResultType.CREATE_ENTRY, f"area: {result}")


async def _search(
    hass: HomeAssistant,
    step: str,
    user_input: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Start one real Home Assistant config flow and run a search step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    _assert(result["type"] is FlowResultType.MENU, f"{step} menu: {result}")
    result = await _configure(hass, result["flow_id"], {"next_step_id": step})
    _assert(result["type"] is FlowResultType.FORM, f"{step} form: {result}")
    if step in {"home", "coordinates"}:
        _assert_nearby_schema(result)
    result = await _configure(hass, result["flow_id"], user_input)
    _assert(
        result["type"] is FlowResultType.FORM
        and str(result.get("step_id", "")).startswith("select_station"),
        f"{step} search: {result}",
    )
    return result["flow_id"], result


async def _configure(
    hass: HomeAssistant,
    flow_id: str,
    user_input: dict[str, Any],
) -> dict[str, Any]:
    """Configure a flow and return its real Home Assistant result."""
    return await hass.config_entries.flow.async_configure(flow_id, user_input)


def _assert_nearby_schema(result: dict[str, Any]) -> None:
    """Check the native selector bounds used by nearby searches."""
    radius = _schema_validator(result, CONF_RADIUS_KM)
    limit = _schema_validator(result, CONF_RESULT_LIMIT)
    radius_config = getattr(radius, "config", None)
    limit_config = getattr(limit, "config", None)
    _assert(
        radius_config is not None
        and _config_value(radius_config, "min") == 0.1
        and _config_value(radius_config, "max") == 200
        and _config_value(radius_config, "step") == 0.1,
        f"invalid radius selector: {radius_config}",
    )
    _assert(
        limit_config is not None
        and _config_value(limit_config, "min") == 1
        and _config_value(limit_config, "max") == 100
        and _config_value(limit_config, "step") == 1,
        f"invalid result-limit selector: {limit_config}",
    )


def _schema_validator(result: dict[str, Any], field: str) -> Any:
    """Find a validator in a Home Assistant voluptuous schema."""
    for key, validator in result["data_schema"].schema.items():
        if getattr(key, "schema", key) == field:
            return validator
    raise AssertionError(f"Missing schema field: {field}")


def _selector_options(result: dict[str, Any]) -> list[str]:
    """Read station IDs from the actual SelectSelector returned by HA."""
    selector = _schema_validator(result, CONF_STATION_ID)
    config = getattr(selector, "config", None)
    options = _config_value(config, "options") or ()
    return [str(option["value"]) for option in options]


def _config_value(config: Any, key: str) -> Any:
    """Read selector config across Home Assistant's dict and object forms."""
    return config.get(key) if isinstance(config, dict) else getattr(config, key, None)


def _configured_station_ids(hass: HomeAssistant) -> set[str]:
    """Return station IDs from Home Assistant's live config-entry registry."""
    return {
        str(entry.data.get(CONF_STATION_ID, ""))
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_STATION_ID)
    }


async def _wait_for_station_states(
    hass: HomeAssistant,
    station_ids: list[str],
    timeout_seconds: int = 180,
) -> dict[str, int]:
    """Wait until every persisted station exposes a real state."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    registry = er.async_get(hass)
    while asyncio.get_running_loop().time() < deadline:
        summary: dict[str, int] = {}
        complete = True
        for station_id in station_ids:
            entity_ids = sorted(
                entity.entity_id
                for entity in registry.entities.values()
                if entity.platform == DOMAIN
                and str(entity.unique_id).startswith(f"{station_id}_")
            )
            states = [hass.states.get(entity_id) for entity_id in entity_ids]
            available = sum(
                state is not None and state.state not in {"unknown", "unavailable"}
                for state in states
            )
            summary[station_id] = available
            if not entity_ids or not available:
                complete = False
        if complete:
            return summary
        await asyncio.sleep(3)
    raise AssertionError(f"Stations did not expose live states: {summary}")


async def _exercise_services(hass: HomeAssistant, station_id: str) -> None:
    """Call real integration services and verify JSON responses/state refresh."""
    for service in (SERVICE_COMPARE_STATIONS, SERVICE_SEARCH_REGISTRY, SERVICE_REFRESH_PRICES):
        _assert(hass.services.has_service(DOMAIN, service), f"Missing service {service}")

    comparison = await hass.services.async_call(
        DOMAIN,
        SERVICE_COMPARE_STATIONS,
        {},
        blocking=True,
        return_response=True,
    )
    _assert(isinstance(comparison, dict), f"Invalid comparison response: {comparison}")
    stations = comparison.get("stations", {})
    _assert(
        any(
            str(data.get("station_id")) == station_id
            for data in stations.values()
            if isinstance(data, dict)
        ),
        f"Station missing from comparison: {comparison}",
    )

    search = await hass.services.async_call(
        DOMAIN,
        SERVICE_SEARCH_REGISTRY,
        {CONF_MUNICIPALITY: "Roma", "limit": 1},
        blocking=True,
        return_response=True,
    )
    _assert(isinstance(search, dict), f"Invalid registry response: {search}")
    _assert(search.get("result_count", 0) <= 1, f"Registry limit failed: {search}")

    refresh = await hass.services.async_call(
        DOMAIN,
        SERVICE_REFRESH_PRICES,
        {"station_ids": [station_id]},
        blocking=True,
        return_response=True,
    )
    _assert(
        isinstance(refresh, dict)
        and refresh.get("refreshed_station_ids") == [station_id]
        and refresh.get("refreshed_count") == 1,
        f"Invalid refresh response: {refresh}",
    )


async def _exercise_reload(hass: HomeAssistant, station_id: str) -> dict[str, Any]:
    """Reload one live entry and make sure its entities remain stable."""
    registry = er.async_get(hass)
    before = sorted(
        entity.entity_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
        and str(entity.unique_id).startswith(f"{station_id}_")
    )
    entry = next(
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if str(entry.data.get(CONF_STATION_ID)) == station_id
    )
    _assert(await hass.config_entries.async_reload(entry.entry_id), "Config entry reload failed")
    await _wait_for_station_states(hass, [station_id])
    after = sorted(
        entity.entity_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
        and str(entity.unique_id).startswith(f"{station_id}_")
    )
    _assert(before == after, f"Entity IDs changed after reload: {before} -> {after}")
    return {"station_id": station_id, "entity_count": len(after)}


async def _write_result(hass: HomeAssistant, result: dict[str, Any]) -> None:
    """Write probe output without blocking Home Assistant's event loop."""
    path = Path(hass.config.path("docker-probe-result.json"))
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    await hass.async_add_executor_job(path.write_text, payload, "utf-8")


def _assert(condition: bool, message: str) -> None:
    """Raise a compact assertion with useful probe context."""
    if not condition:
        raise AssertionError(message)

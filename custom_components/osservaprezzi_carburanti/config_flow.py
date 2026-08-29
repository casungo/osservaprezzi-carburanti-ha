from __future__ import annotations

import logging
from functools import partial
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT, ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .api import fetch_station_data
from .const import (
    DOMAIN,
    CONF_CRON_EXPRESSION,
    CONF_PRICE_STALE_HOURS,
    CONF_STATION_ID,
    DEFAULT_CRON_EXPRESSION,
    DEFAULT_PRICE_STALE_HOURS,
    PRICE_STALE_HOUR_OPTIONS,
)
from .cron_helper import get_next_run_time, validate_cron_expression
from .csv_manager import RegistrySnapshot, RegistryUnavailableError, get_shared_csv_manager
from .discovery import StationCandidate, find_nearby_stations, find_stations_by_area

_LOGGER = logging.getLogger(__name__)

CONF_RADIUS_KM = "radius_km"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_MUNICIPALITY = "municipality"
CONF_PROVINCE = "province"
CONF_TEXT_FILTER = "text_filter"
CONF_STATION_TYPE = "station_type"
CONF_RESULT_LIMIT = "result_limit"
DEFAULT_RADIUS_KM = 5
RADIUS_OPTIONS_KM = (2, 5, 10, 20, 50)
MAX_NEARBY_STATIONS = 20
RESULT_LIMIT_OPTIONS = (5, 10, 20)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidStation(HomeAssistantError):
    """Error to indicate there is an invalid station."""


async def _validate_station(hass: HomeAssistant, station_id: str) -> dict[str, Any]:
    """Validate the station_id by making an API call."""
    normalized_station_id = station_id.strip()
    if not normalized_station_id:
        raise InvalidStation("Station ID is empty")

    try:
        data = await fetch_station_data(hass, normalized_station_id)
        if not data.get("id") or not data.get("name"):
            raise InvalidStation("Invalid station data received")
        return {"name": data["name"]}
    except aiohttp.ClientResponseError as err:
        if err.status == 404:
            raise InvalidStation("Station not found")
        raise CannotConnect(f"Service error: {err.status}") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect(f"Connection error: {err}")


class OsservaprezziCarburantiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle the config flow for Osservaprezzi Carburanti."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        return OptionsFlowHandler(config_entry)

    async def _async_create_station_entry(
        self,
        station_id: str,
        station_info: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Validate a station and create its config entry."""
        normalized_station_id = station_id.strip()
        await self.async_set_unique_id(f"station_{normalized_station_id}")
        self._abort_if_unique_id_configured()

        station_info = station_info or await _validate_station(
            self.hass, normalized_station_id
        )
        return self.async_create_entry(
            title=station_info["name"],
            data={CONF_STATION_ID: normalized_station_id},
        )

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create one entry requested by a batch selection."""
        if user_input is None:
            return self.async_abort(reason="invalid_station")
        return await self._async_create_station_entry(
            str(user_input[CONF_STATION_ID]),
            {"name": str(user_input["name"])},
        )

    async def _handle_station_input(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> ConfigFlowResult:
        """Handle station ID input for any config flow step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                station_id = str(user_input.get(CONF_STATION_ID, ""))
                return await self._async_create_station_entry(station_id)
            except InvalidStation:
                errors["base"] = "invalid_station"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except (TypeError, ValueError) as err:
                _LOGGER.exception("Unexpected station validation error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer local discovery or manual station ID setup."""
        if user_input is not None and CONF_STATION_ID in user_input:
            return await self._handle_station_input(user_input, "station_id")
        return self.async_show_menu(
            step_id="user",
            menu_options=["home", "coordinates", "area", "station_id"],
        )

    async def async_step_station_id(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a station from its Osservaprezzi ID."""
        return await self._handle_station_input(user_input, "station_id")

    async def async_step_home(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find stations near Home Assistant's configured home."""
        errors: dict[str, str] = {}
        if user_input is not None:
            latitude = getattr(self.hass.config, "latitude", None)
            longitude = getattr(self.hass.config, "longitude", None)
            if (
                isinstance(latitude, bool)
                or isinstance(longitude, bool)
                or not isinstance(latitude, (int, float))
                or not isinstance(longitude, (int, float))
            ):
                errors["base"] = "home_location_unavailable"
            else:
                result, error = await self._async_search_nearby(
                    latitude=float(latitude),
                    longitude=float(longitude),
                    user_input=user_input,
                    source_step="home",
                )
                if result is not None:
                    return result
                if error is not None:
                    errors["base"] = error

        return self.async_show_form(
            step_id="home",
            data_schema=self._nearby_search_schema(),
            errors=errors,
        )

    async def async_step_coordinates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find stations near manually supplied coordinates."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                latitude = float(user_input[CONF_LATITUDE])
                longitude = float(user_input[CONF_LONGITUDE])
                if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                    raise ValueError("Coordinates are out of range")
                result, error = await self._async_search_nearby(
                    latitude=latitude,
                    longitude=longitude,
                    user_input=user_input,
                    source_step="coordinates",
                )
                if result is not None:
                    return result
                if error is not None:
                    errors["base"] = error
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_location"

        return self.async_show_form(
            step_id="coordinates",
            data_schema=self._nearby_search_schema(
                {
                    vol.Required(CONF_LATITUDE): vol.Coerce(float),
                    vol.Required(CONF_LONGITUDE): vol.Coerce(float),
                }
            ),
            errors=errors,
        )

    async def async_step_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find stations by municipality and optional province."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                snapshot = await get_shared_csv_manager(
                    self.hass
                ).async_ensure_registry(allow_stale=True)
                limit, text_filter, station_type = self._search_filters(user_input)
                candidates = await self.hass.async_add_executor_job(
                    partial(
                        find_stations_by_area,
                        snapshot.stations,
                        municipality=str(user_input[CONF_MUNICIPALITY]),
                        province=str(user_input.get(CONF_PROVINCE, "")),
                        text_filter=text_filter,
                        station_type=station_type,
                        limit=limit,
                    )
                )
                if candidates:
                    self._store_search_results(candidates, snapshot, "area")
                    return await self._async_step_select_station()
                errors["base"] = "no_stations_found"
            except RegistryUnavailableError:
                errors["base"] = "registry_unavailable"
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.exception("Unexpected area station search error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="area",
            data_schema=self._area_search_schema(),
            errors=errors,
        )

    async def _async_search_nearby(
        self,
        *,
        latitude: float,
        longitude: float,
        user_input: dict[str, Any],
        source_step: str,
    ) -> tuple[ConfigFlowResult | None, str | None]:
        """Run a local coordinate search and return a flow result or error key."""
        try:
            radius_km = int(user_input[CONF_RADIUS_KM])
            if radius_km not in RADIUS_OPTIONS_KM:
                raise ValueError("Unsupported nearby search radius")
            limit, text_filter, station_type = self._search_filters(user_input)
            snapshot = await get_shared_csv_manager(
                self.hass
            ).async_ensure_registry(allow_stale=True)
            candidates = await self.hass.async_add_executor_job(
                partial(
                    find_nearby_stations,
                    snapshot.stations,
                    latitude=latitude,
                    longitude=longitude,
                    radius_km=radius_km,
                    limit=limit,
                    text_filter=text_filter,
                    station_type=station_type,
                )
            )
            if not candidates:
                return None, "no_stations_found"
            self._store_search_results(candidates, snapshot, source_step)
            return await self._async_step_select_station(), None
        except RegistryUnavailableError:
            return None, "registry_unavailable"
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.exception("Unexpected nearby station search error: %s", err)
            return None, "unknown"

    @staticmethod
    def _search_filters(user_input: dict[str, Any]) -> tuple[int, str | None, str | None]:
        """Validate and normalize common local-search fields."""
        limit = int(user_input.get(CONF_RESULT_LIMIT, MAX_NEARBY_STATIONS))
        if limit not in RESULT_LIMIT_OPTIONS:
            raise ValueError("Unsupported result limit")
        text_filter = str(user_input.get(CONF_TEXT_FILTER, "")).strip() or None
        station_type = str(user_input.get(CONF_STATION_TYPE, "")).strip() or None
        return limit, text_filter, station_type

    @staticmethod
    def _common_search_fields() -> dict[Any, Any]:
        """Return common optional registry search fields."""
        return {
            vol.Optional(CONF_TEXT_FILTER, default=""): str,
            vol.Optional(CONF_STATION_TYPE, default=""): str,
            vol.Required(
                CONF_RESULT_LIMIT,
                default=MAX_NEARBY_STATIONS,
            ): vol.In(RESULT_LIMIT_OPTIONS),
        }

    @classmethod
    def _nearby_search_schema(
        cls,
        extra_fields: dict[Any, Any] | None = None,
    ) -> vol.Schema:
        """Build a coordinate-based search schema."""
        fields = dict(extra_fields or {})
        fields[
            vol.Required(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM)
        ] = vol.In(RADIUS_OPTIONS_KM)
        fields.update(cls._common_search_fields())
        return vol.Schema(fields)

    @classmethod
    def _area_search_schema(cls) -> vol.Schema:
        """Build a municipality-based search schema."""
        fields: dict[Any, Any] = {
            vol.Required(CONF_MUNICIPALITY): str,
            vol.Optional(CONF_PROVINCE, default=""): str,
        }
        fields.update(cls._common_search_fields())
        return vol.Schema(fields)

    def _store_search_results(
        self,
        candidates: tuple[StationCandidate, ...],
        snapshot: RegistrySnapshot,
        source_step: str,
    ) -> None:
        """Keep public station candidates and registry status for selection."""
        self._nearby_candidates = candidates
        self._registry_is_stale = snapshot.is_stale
        self._registry_updated = (
            snapshot.updated_at.isoformat() if snapshot.updated_at is not None else "—"
        )
        self._search_step_id = source_step

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a station from fresh nearby results."""
        return await self._async_step_select_station(user_input)

    async def async_step_select_station_stale(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a station from cached nearby results."""
        return await self._async_step_select_station(user_input)

    async def _async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a nearby station selection."""
        candidates: tuple[StationCandidate, ...] = getattr(
            self, "_nearby_candidates", ()
        )
        if not candidates:
            source_step = getattr(self, "_search_step_id", "home")
            return await getattr(self, f"async_step_{source_step}")()

        errors: dict[str, str] = {}
        configured_ids = {
            str(entry.data.get(CONF_STATION_ID, ""))
            for entry in self._async_current_entries()
        }
        if user_input is not None:
            try:
                selected_value = user_input.get(CONF_STATION_ID, [])
                selected_ids = (
                    [selected_value]
                    if isinstance(selected_value, str)
                    else [str(station_id) for station_id in selected_value]
                )
                candidate_ids = {candidate.station_id for candidate in candidates}
                if not selected_ids or not set(selected_ids) <= candidate_ids:
                    raise InvalidStation("Station is not in the current nearby results")

                new_selected_ids = [
                    station_id
                    for station_id in selected_ids
                    if station_id not in configured_ids
                ]
                if not new_selected_ids:
                    errors["base"] = "already_configured"
                else:
                    station_info = {
                        station_id: await _validate_station(self.hass, station_id)
                        for station_id in new_selected_ids
                    }
                    for station_id in new_selected_ids[1:]:
                        await self.hass.config_entries.flow.async_init(
                            DOMAIN,
                            context={"source": SOURCE_IMPORT},
                            data={
                                CONF_STATION_ID: station_id,
                                "name": station_info[station_id]["name"],
                            },
                        )
                    return await self._async_create_station_entry(
                        new_selected_ids[0], station_info[new_selected_ids[0]]
                    )
            except InvalidStation:
                errors["base"] = "invalid_station"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except (TypeError, ValueError) as err:
                _LOGGER.exception("Unexpected nearby station selection error: %s", err)
                errors["base"] = "unknown"

        options = [
            SelectOptionDict(
                value=candidate.station_id,
                label=self._format_candidate_label(candidate),
            )
            for candidate in candidates
        ]
        step_id = (
            "select_station_stale"
            if getattr(self, "_registry_is_stale", False)
            else "select_station"
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_ID): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "registry_updated": getattr(self, "_registry_updated", "—"),
                "configured_count": str(
                    sum(candidate.station_id in configured_ids for candidate in candidates)
                ),
                "result_count": str(len(candidates)),
            },
        )

    @staticmethod
    def _format_candidate_label(candidate: StationCandidate) -> str:
        """Build a compact, accessible label for a station choice."""
        parts: list[str] = []
        if candidate.distance_km is not None:
            distance = (
                f"{candidate.distance_km:.1f}"
                if candidate.distance_km < 10
                else f"{candidate.distance_km:.0f}"
            )
            parts.append(f"{distance} km")
        parts.append(candidate.name)
        if candidate.brand and candidate.brand.casefold() not in candidate.name.casefold():
            parts.append(candidate.brand)
        if candidate.station_type:
            parts.append(candidate.station_type)
        location = candidate.address or ", ".join(
            value for value in (candidate.municipality, candidate.province) if value
        )
        if location:
            parts.append(location)
        parts.append(f"ID {candidate.station_id}")
        label = " · ".join(parts)
        if len(label) <= 64:
            return label
        suffix = f" · ID {candidate.station_id}"
        prefix = " · ".join(parts[:-1])
        return f"{prefix[: 64 - len(suffix) - 1].rstrip()}…{suffix}"

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow an existing entry to point at another station."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            station_id = str(user_input.get(CONF_STATION_ID, "")).strip()
            try:
                station_info = await _validate_station(self.hass, station_id)
                unique_id = f"station_{station_id}"
                duplicate = any(
                    other.entry_id != entry.entry_id and other.unique_id == unique_id
                    for other in self._async_current_entries()
                )
                if duplicate:
                    errors["base"] = "already_configured"
                else:
                    return self.async_update_and_abort(
                        entry,
                        unique_id=unique_id,
                        title=station_info["name"],
                        data_updates={CONF_STATION_ID: station_id},
                    )
            except InvalidStation:
                errors["base"] = "invalid_station"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except (TypeError, ValueError) as err:
                _LOGGER.exception("Unexpected station reconfiguration error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_STATION_ID,
                        default=entry.data[CONF_STATION_ID],
                    ): str
                }
            ),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlowWithConfigEntry):
    """Handle an options flow for Osservaprezzi Carburanti."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cron_expr = user_input[CONF_CRON_EXPRESSION]
            old_cron_expr = self.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION)
            try:
                stale_hours = int(
                    user_input.get(
                        CONF_PRICE_STALE_HOURS,
                        DEFAULT_PRICE_STALE_HOURS,
                    )
                )
            except (TypeError, ValueError):
                errors["base"] = "invalid_stale_hours"
            else:
                if stale_hours not in PRICE_STALE_HOUR_OPTIONS:
                    errors["base"] = "invalid_stale_hours"
                elif validate_cron_expression(cron_expr):
                    if cron_expr != old_cron_expr:
                        _LOGGER.info(
                            "Cron expression updated from '%s' to '%s' for %s",
                            old_cron_expr,
                            cron_expr,
                            self.config_entry.title,
                        )
                    return self.async_create_entry(
                        title="",
                        data={
                            CONF_CRON_EXPRESSION: cron_expr,
                            CONF_PRICE_STALE_HOURS: stale_hours,
                        },
                    )
                else:
                    _LOGGER.warning(
                        "Invalid cron expression submitted: '%s' for %s",
                        cron_expr,
                        self.config_entry.title,
                    )
                    errors["base"] = "invalid_cron_expression"

        preview_expression = self.options.get(
            CONF_CRON_EXPRESSION,
            DEFAULT_CRON_EXPRESSION,
        )
        try:
            next_run = get_next_run_time(preview_expression).isoformat()
        except (ImportError, TypeError, ValueError):
            next_run = "—"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CRON_EXPRESSION,
                    default=self.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION),
                ): str,
                vol.Required(
                    CONF_PRICE_STALE_HOURS,
                    default=self.options.get(
                        CONF_PRICE_STALE_HOURS,
                        DEFAULT_PRICE_STALE_HOURS,
                    ),
                ): vol.In(PRICE_STALE_HOUR_OPTIONS),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"next_run": next_run},
        )

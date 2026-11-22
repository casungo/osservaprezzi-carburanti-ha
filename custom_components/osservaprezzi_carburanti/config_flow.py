from __future__ import annotations

import logging
from datetime import datetime
from functools import partial
from typing import Any, cast

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)
from homeassistant.util import dt as dt_util

from .api import fetch_station_data
from .const import (
    CONF_CRON_EXPRESSION,
    CONF_PRICE_STALE_HOURS,
    CONF_STATION_ID,
    CONF_TRACKED_FUELS,
    CONF_TRACKED_SERVICE_MODES,
    DEFAULT_CRON_EXPRESSION,
    DEFAULT_PRICE_STALE_HOURS,
    DOMAIN,
    HUB_UNIQUE_ID,
    PRICE_STALE_HOUR_OPTIONS,
    SUBENTRY_TYPE_STATION,
)
from .cron_helper import validate_cron_expression
from .csv_manager import RegistrySnapshot, RegistryUnavailableError, get_shared_csv_manager
from .data_helpers import as_coordinate, parse_coordinate
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
DEFAULT_RESULT_LIMIT = 20
MAX_RADIUS_KM = 200
MAX_RESULT_LIMIT = 100


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class RateLimited(CannotConnect):
    """Error to indicate that the upstream service rate-limited the request."""


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
            raise InvalidStation("Station not found") from err
        if err.status == 429:
            raise RateLimited("Service rate limit exceeded") from err
        raise CannotConnect(f"Service error: {err.status}") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect(f"Connection error: {err}") from err


class OsservaprezziCarburantiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Osservaprezzi Carburanti."""

    VERSION = 3
    _multiple_selection = True

    def __init__(self) -> None:
        """Keep search suggestions only for the lifetime of this flow."""
        super().__init__()
        self._initialize_discovery()

    def _initialize_discovery(self) -> None:
        """Initialize discovery state for either kind of flow."""
        self._search_inputs: dict[str, dict[str, Any]] = {}
        self._nearby_candidates: tuple[StationCandidate, ...] = ()
        self._registry_is_stale = False
        self._registry_updated = "—"
        self._search_step_id = "home"
        self._results_limited = False
        self._result_limit = DEFAULT_RESULT_LIMIT
        self._failed_station = ""
        self._failed_station_id = ""

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: config_entries.ConfigEntry,
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Return the supported station subentry flow."""
        return {SUBENTRY_TYPE_STATION: StationSubentryFlow}

    def _configured_station_ids(self) -> set[str]:
        return set()

    async def _async_create_selected_stations(
        self,
        station_ids: list[str],
        station_info: dict[str, dict[str, Any]],
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(HUB_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Osservaprezzi Carburanti",
            data={},
            subentries=[
                config_entries.ConfigSubentryData(
                    data={CONF_STATION_ID: station_id},
                    subentry_type=SUBENTRY_TYPE_STATION,
                    title=station_info[station_id]["name"],
                    unique_id=station_id,
                )
                for station_id in station_ids
            ],
        )

    async def _async_create_station_entry(
        self,
        station_id: str,
        station_info: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        normalized = station_id.strip()
        station_info = station_info or await _validate_station(self.hass, normalized)
        if normalized in self._configured_station_ids():
            return self.async_abort(reason="already_configured")
        return await self._async_create_selected_stations([normalized], {normalized: station_info})

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
                errors[CONF_STATION_ID] = "invalid_station"
            except RateLimited:
                errors[CONF_STATION_ID] = "rate_limited"
            except CannotConnect:
                errors[CONF_STATION_ID] = "cannot_connect"
            except (TypeError, ValueError) as err:
                _LOGGER.exception("Unexpected station validation error: %s", err)
                errors[CONF_STATION_ID] = "unknown"

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_STATION_ID): str}), user_input
            ),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer local discovery or manual station ID setup."""
        await self.async_set_unique_id(HUB_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        if user_input is not None and CONF_STATION_ID in user_input:
            return await self._handle_station_input(user_input, "station_id")
        return self.async_show_menu(
            step_id="user",
            menu_options=["home", "coordinates", "zone", "area", "station_id"],
        )

    async def async_step_station_id(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a station from its Osservaprezzi ID."""
        return await self._handle_station_input(user_input, "station_id")

    async def async_step_home(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Find stations near Home Assistant's configured home."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._search_inputs["home"] = dict(user_input)
            latitude = as_coordinate(self.hass.config.latitude, -90, 90)
            longitude = as_coordinate(self.hass.config.longitude, -180, 180)
            if latitude is None or longitude is None:
                errors["base"] = "home_location_unavailable"
            else:
                result, error = await self._async_search_nearby(
                    latitude=latitude,
                    longitude=longitude,
                    user_input=user_input,
                    source_step="home",
                )
                if result is not None:
                    return result
                errors["base"] = cast(str, error)

        return self.async_show_form(
            step_id="home",
            data_schema=self.add_suggested_values_to_schema(
                self._nearby_search_schema(),
                self._search_inputs.get("home"),
            ),
            errors=errors,
        )

    async def async_step_coordinates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find stations near manually supplied coordinates."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._search_inputs["coordinates"] = dict(user_input)
            try:
                location = user_input.get("location", user_input)
                latitude = parse_coordinate(location[CONF_LATITUDE], -90, 90)
                longitude = parse_coordinate(location[CONF_LONGITUDE], -180, 180)
                if latitude is None or longitude is None:
                    raise ValueError("Coordinates are out of range")
                result, error = await self._async_search_nearby(
                    latitude=latitude,
                    longitude=longitude,
                    user_input=user_input,
                    source_step="coordinates",
                )
                if result is not None:
                    return result
                errors["base"] = cast(str, error)
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_location"

        return self.async_show_form(
            step_id="coordinates",
            data_schema=self.add_suggested_values_to_schema(
                self._nearby_search_schema(
                    {
                        vol.Required("location"): LocationSelector(
                            LocationSelectorConfig(radius=False)
                        ),
                    }
                ),
                self._search_inputs.get("coordinates"),
            ),
            errors=errors,
        )

    async def async_step_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Find stations near a Home Assistant zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._search_inputs["zone"] = dict(user_input)
            state = self.hass.states.get(user_input.get("zone", ""))
            attributes = state.attributes if state is not None else {}
            latitude = as_coordinate(attributes.get(CONF_LATITUDE), -90, 90)
            longitude = as_coordinate(attributes.get(CONF_LONGITUDE), -180, 180)
            if latitude is None or longitude is None:
                errors["base"] = "invalid_location"
            else:
                result, error = await self._async_search_nearby(
                    latitude=latitude,
                    longitude=longitude,
                    user_input=user_input,
                    source_step="zone",
                )
                if result is not None:
                    return result
                errors["base"] = cast(str, error)
        return self.async_show_form(
            step_id="zone",
            data_schema=self.add_suggested_values_to_schema(
                self._nearby_search_schema(
                    {
                        vol.Required("zone"): EntitySelector(EntitySelectorConfig(domain="zone")),
                    }
                ),
                self._search_inputs.get("zone"),
            ),
            errors=errors,
        )

    async def async_step_area(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Find stations by municipality and optional province."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._search_inputs["area"] = dict(user_input)
            try:
                snapshot = await get_shared_csv_manager(self.hass).async_ensure_registry(
                    allow_stale=True
                )
                limit, text_filter, station_type = self._search_filters(user_input)
                candidates = await self.hass.async_add_executor_job(
                    partial(
                        find_stations_by_area,
                        snapshot.stations,
                        municipality=str(user_input[CONF_MUNICIPALITY]),
                        province=str(user_input.get(CONF_PROVINCE) or ""),
                        text_filter=text_filter,
                        station_type=station_type,
                        limit=limit + 1,
                    )
                )
                if candidates:
                    self._store_search_results(
                        candidates[:limit], snapshot, "area", len(candidates) > limit, limit
                    )
                    return await self._async_step_select_station()
                errors["base"] = "no_stations_found"
            except RegistryUnavailableError:
                errors["base"] = "registry_unavailable"
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.exception("Unexpected area station search error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="area",
            data_schema=self.add_suggested_values_to_schema(
                self._area_search_schema(),
                self._search_inputs.get("area"),
            ),
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
            radius_km = float(user_input[CONF_RADIUS_KM])
            if not 0 < radius_km <= MAX_RADIUS_KM:
                raise ValueError("Nearby search radius is out of range")
            limit, text_filter, station_type = self._search_filters(user_input)
            snapshot = await get_shared_csv_manager(self.hass).async_ensure_registry(
                allow_stale=True
            )
            candidates = await self.hass.async_add_executor_job(
                partial(
                    find_nearby_stations,
                    snapshot.stations,
                    latitude=latitude,
                    longitude=longitude,
                    radius_km=radius_km,
                    limit=limit + 1,
                    text_filter=text_filter,
                    station_type=station_type,
                )
            )
            if not candidates:
                return None, "no_stations_found"
            self._store_search_results(
                candidates[:limit],
                snapshot,
                source_step,
                len(candidates) > limit,
                limit,
            )
            return await self._async_step_select_station(), None
        except RegistryUnavailableError:
            return None, "registry_unavailable"
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.exception("Unexpected nearby station search error: %s", err)
            return None, "unknown"

    @staticmethod
    def _search_filters(user_input: dict[str, Any]) -> tuple[int, str | None, str | None]:
        """Validate and normalize common local-search fields."""
        limit = int(user_input.get(CONF_RESULT_LIMIT, DEFAULT_RESULT_LIMIT))
        if not 0 < limit <= MAX_RESULT_LIMIT:
            raise ValueError("Result limit is out of range")
        text_filter = str(user_input.get(CONF_TEXT_FILTER) or "").strip() or None
        station_type = str(user_input.get(CONF_STATION_TYPE) or "").strip() or None
        return limit, text_filter, station_type

    def _common_search_fields(self) -> dict[Any, Any]:
        """Return common optional registry search fields."""
        station_types = self._registry_station_types()
        station_type_selector: Any = (
            SelectSelector(SelectSelectorConfig(options=list(station_types), custom_value=True))
            if station_types
            else str
        )
        return {
            vol.Optional(CONF_TEXT_FILTER, default=""): str,
            vol.Optional(CONF_STATION_TYPE, default=""): station_type_selector,
            vol.Required(
                CONF_RESULT_LIMIT,
                default=DEFAULT_RESULT_LIMIT,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=MAX_RESULT_LIMIT,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }

    def _nearby_search_schema(
        self,
        extra_fields: dict[Any, Any] | None = None,
    ) -> vol.Schema:
        """Build a coordinate-based search schema."""
        fields = dict(extra_fields or {})
        fields[vol.Required(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM)] = NumberSelector(
            NumberSelectorConfig(
                min=0.1,
                max=MAX_RADIUS_KM,
                step=0.1,
                unit_of_measurement="km",
                mode=NumberSelectorMode.BOX,
            )
        )
        fields.update(self._common_search_fields())
        return vol.Schema(fields)

    def _area_search_schema(self) -> vol.Schema:
        """Build a municipality-based search schema."""
        fields: dict[Any, Any] = {
            vol.Required(CONF_MUNICIPALITY): str,
            vol.Optional(CONF_PROVINCE, default=""): str,
        }
        fields.update(self._common_search_fields())
        return vol.Schema(fields)

    def _registry_station_types(self) -> tuple[str, ...]:
        """Read station-type suggestions from the in-memory registry cache."""
        return get_shared_csv_manager(self.hass).registry_station_types()

    def _store_search_results(
        self,
        candidates: tuple[StationCandidate, ...],
        snapshot: RegistrySnapshot,
        source_step: str,
        results_limited: bool = False,
        result_limit: int = DEFAULT_RESULT_LIMIT,
    ) -> None:
        """Keep public station candidates and registry status for selection."""
        self._nearby_candidates = tuple(candidates)
        self._registry_is_stale = snapshot.is_stale
        self._registry_updated = self._format_datetime(snapshot.updated_at)
        self._search_step_id = source_step
        self._results_limited = results_limited
        self._result_limit = result_limit

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a station from fresh nearby results."""
        return await self._async_step_select_station(user_input)

    async def _async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a nearby station selection."""
        candidates = self._nearby_candidates
        if not candidates:
            return await self._async_return_to_search_step()

        errors: dict[str, str] = {}
        description_placeholders = self._selection_placeholders()
        configured_ids = self._configured_station_ids()
        candidate_ids = {candidate.station_id for candidate in candidates}
        available_candidates = tuple(
            candidate for candidate in candidates if candidate.station_id not in configured_ids
        )
        available_ids = {candidate.station_id for candidate in available_candidates}
        if not available_candidates:
            result = await self._async_return_to_search_step()
            result["errors"] = {"base": "all_stations_configured"}
            return result

        selected_ids: list[str] = []
        if user_input is not None:
            try:
                selected_value = user_input.get(CONF_STATION_ID, [])
                selected_ids = self._deduplicate_station_ids(selected_value)
                if (
                    not selected_ids
                    or not set(selected_ids) <= candidate_ids
                    or (not self._multiple_selection and len(selected_ids) != 1)
                ):
                    raise InvalidStation("Station is not in the current nearby results")

                new_selected_ids = [
                    station_id for station_id in selected_ids if station_id not in configured_ids
                ]
                if not new_selected_ids:
                    errors["base"] = "already_configured"
                else:
                    station_info: dict[str, dict[str, Any]] = {}
                    for station_id in new_selected_ids:
                        try:
                            station_info[station_id] = await _validate_station(
                                self.hass, station_id
                            )
                        except (InvalidStation, CannotConnect) as err:
                            if len(selected_ids) > 1:
                                failed_candidate = next(
                                    candidate
                                    for candidate in candidates
                                    if candidate.station_id == station_id
                                )
                                self._failed_station = failed_candidate.name
                                self._failed_station_id = station_id
                                description_placeholders.update(
                                    {
                                        "failed_station": self._failed_station,
                                        "failed_station_id": station_id,
                                    }
                                )
                                errors["base"] = "station_validation_failed"
                            elif isinstance(err, InvalidStation):
                                errors["base"] = "invalid_station"
                            elif isinstance(err, RateLimited):
                                errors["base"] = "rate_limited"
                            else:
                                errors["base"] = "cannot_connect"
                            if isinstance(err, InvalidStation):
                                self._nearby_candidates = tuple(
                                    candidate
                                    for candidate in candidates
                                    if candidate.station_id != station_id
                                )
                                available_candidates = tuple(
                                    candidate
                                    for candidate in available_candidates
                                    if candidate.station_id != station_id
                                )
                                available_ids.discard(station_id)
                            _LOGGER.warning(
                                "Unable to validate selected station %s: %s", station_id, err
                            )
                            break
                    else:
                        return await self._async_create_selected_stations(
                            new_selected_ids, station_info
                        )
            except InvalidStation:
                errors["base"] = "invalid_station"
            except RateLimited:
                errors["base"] = "rate_limited"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except (TypeError, ValueError) as err:
                _LOGGER.exception("Unexpected nearby station selection error: %s", err)
                errors["base"] = "unknown"

        if not available_candidates:
            result = await self._async_return_to_search_step()
            result["errors"] = errors
            return result

        options = [
            SelectOptionDict(
                value=candidate.station_id,
                label=self._format_candidate_label(candidate),
            )
            for candidate in available_candidates
        ]
        description_placeholders.update(
            {
                "failed_station": self._failed_station,
                "failed_station_id": self._failed_station_id,
            }
        )
        return self.async_show_form(
            step_id="select_station",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_STATION_ID): SelectSelector(
                            SelectSelectorConfig(options=options, multiple=self._multiple_selection)
                        )
                    }
                ),
                {
                    CONF_STATION_ID: (
                        [station_id for station_id in selected_ids if station_id in available_ids]
                        if self._multiple_selection
                        else selected_ids[0]
                        if selected_ids and selected_ids[0] in available_ids
                        else None
                    )
                }
                if user_input is not None
                else None,
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    @staticmethod
    def _deduplicate_station_ids(selected_value: Any) -> list[str]:
        """Normalize a selector value and preserve its first-seen order."""
        raw_ids = [selected_value] if isinstance(selected_value, str) else selected_value
        selected_ids: list[str] = []
        seen: set[str] = set()
        for station_id in raw_ids:
            normalized_id = str(station_id)
            if normalized_id not in seen:
                seen.add(normalized_id)
                selected_ids.append(normalized_id)
        return selected_ids

    async def _async_return_to_search_step(self) -> ConfigFlowResult:
        """Return to the known search step without dynamic method dispatch."""
        search_steps = {
            "home": self.async_step_home,
            "coordinates": self.async_step_coordinates,
            "zone": self.async_step_zone,
            "area": self.async_step_area,
        }
        return await search_steps.get(self._search_step_id, self.async_step_home)()

    def _selection_placeholders(self) -> dict[str, str]:
        """Build selection description values for the current search."""
        configured_ids = self._configured_station_ids()
        return {
            "registry_updated": self._registry_updated,
            "configured_count": str(
                sum(candidate.station_id in configured_ids for candidate in self._nearby_candidates)
            ),
            "result_count": str(len(self._nearby_candidates)),
            "result_limit": str(self._result_limit),
        }

    @staticmethod
    def _format_datetime(value: datetime | None) -> str:
        """Format a timestamp in the Home Assistant local timezone."""
        return dt_util.as_local(value).strftime("%Y-%m-%d %H:%M %Z") if value else "—"

    @staticmethod
    def _format_candidate_label(candidate: StationCandidate) -> str:
        """Build a compact, accessible label for a station choice."""
        prefix = ""
        if candidate.distance_km is not None:
            distance = (
                f"{candidate.distance_km:.1f}"
                if candidate.distance_km < 10
                else f"{candidate.distance_km:.0f}"
            )
            prefix = f"{distance} km · "
        suffix = f" · ID {candidate.station_id}"
        location = candidate.address or ", ".join(
            value for value in (candidate.municipality, candidate.province) if value
        )
        parts = [candidate.name]
        if location:
            parts.append(location)
        if candidate.brand and candidate.brand.casefold() not in candidate.name.casefold():
            parts.append(candidate.brand)
        if candidate.station_type:
            parts.append(candidate.station_type)
        label = prefix + " · ".join(parts) + suffix
        if len(label) <= 64:
            return label

        def shorten(value: str, budget: int) -> str:
            return value if len(value) <= budget else value[: budget - 1].rstrip() + "…"

        budget = 64 - len(prefix) - len(suffix)
        if location:
            name = shorten(candidate.name, min(18, budget // 2))
            location = shorten(location, budget - len(name) - 3)
            return prefix + name + " · " + location + suffix
        return prefix + shorten(candidate.name, budget) + suffix


class StationSubentryFlow(config_entries.ConfigSubentryFlow):
    """Discover, add and reconfigure a station on the hub."""

    _multiple_selection = False
    _initialize_discovery = OsservaprezziCarburantiConfigFlow._initialize_discovery
    _handle_station_input = OsservaprezziCarburantiConfigFlow._handle_station_input
    _async_create_station_entry = OsservaprezziCarburantiConfigFlow._async_create_station_entry
    async_step_station_id = OsservaprezziCarburantiConfigFlow.async_step_station_id
    async_step_home = OsservaprezziCarburantiConfigFlow.async_step_home
    async_step_coordinates = OsservaprezziCarburantiConfigFlow.async_step_coordinates
    async_step_zone = OsservaprezziCarburantiConfigFlow.async_step_zone
    async_step_area = OsservaprezziCarburantiConfigFlow.async_step_area
    _async_search_nearby = OsservaprezziCarburantiConfigFlow._async_search_nearby
    _search_filters = staticmethod(OsservaprezziCarburantiConfigFlow._search_filters)
    _common_search_fields = OsservaprezziCarburantiConfigFlow._common_search_fields
    _nearby_search_schema = OsservaprezziCarburantiConfigFlow._nearby_search_schema
    _area_search_schema = OsservaprezziCarburantiConfigFlow._area_search_schema
    _registry_station_types = OsservaprezziCarburantiConfigFlow._registry_station_types
    _store_search_results = OsservaprezziCarburantiConfigFlow._store_search_results
    async_step_select_station = OsservaprezziCarburantiConfigFlow.async_step_select_station
    _async_step_select_station = OsservaprezziCarburantiConfigFlow._async_step_select_station
    _deduplicate_station_ids = staticmethod(
        OsservaprezziCarburantiConfigFlow._deduplicate_station_ids
    )
    _async_return_to_search_step = OsservaprezziCarburantiConfigFlow._async_return_to_search_step
    _selection_placeholders = OsservaprezziCarburantiConfigFlow._selection_placeholders
    _format_datetime = staticmethod(OsservaprezziCarburantiConfigFlow._format_datetime)
    _format_candidate_label = staticmethod(
        OsservaprezziCarburantiConfigFlow._format_candidate_label
    )

    def __init__(self) -> None:
        """Initialize the station discovery state."""
        super().__init__()
        self._initialize_discovery()  # type: ignore[misc]

    def _configured_station_ids(self) -> set[str]:
        return {
            str(subentry.data.get(CONF_STATION_ID, ""))
            for subentry in self._get_entry().subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_STATION
        }

    async def _async_create_selected_stations(
        self,
        station_ids: list[str],
        station_info: dict[str, dict[str, Any]],
    ) -> Any:
        station_id = station_ids[0]
        if station_id in self._configured_station_ids():
            return self.async_abort(reason="already_configured")
        return self.async_create_entry(
            title=station_info[station_id]["name"],
            data={CONF_STATION_ID: station_id},
            unique_id=station_id,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        """Offer discovery and manual entry for a new station."""
        if user_input is not None and CONF_STATION_ID in user_input:
            return await self._handle_station_input(user_input, "station_id")  # type: ignore[misc]
        return self.async_show_menu(
            step_id="user", menu_options=["home", "coordinates", "zone", "area", "station_id"]
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> Any:
        """Edit the monitored station and its refresh and tracking settings."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        current = subentry.data
        errors: dict[str, str] = {}
        if user_input is not None:
            station_id = str(user_input.get(CONF_STATION_ID, "")).strip()
            cron = str(user_input.get(CONF_CRON_EXPRESSION, "")).strip()
            try:
                stale_hours = int(user_input.get(CONF_PRICE_STALE_HOURS, 0))
            except TypeError, ValueError:
                stale_hours = 0
            if not station_id:
                errors[CONF_STATION_ID] = "invalid_station"
            elif any(
                other.subentry_id != subentry.subentry_id and other.unique_id == station_id
                for other in entry.subentries.values()
            ):
                errors[CONF_STATION_ID] = "already_configured"
            if not validate_cron_expression(cron):
                errors[CONF_CRON_EXPRESSION] = "invalid_cron_expression"
            if stale_hours not in PRICE_STALE_HOUR_OPTIONS:
                errors[CONF_PRICE_STALE_HOURS] = "invalid_stale_hours"
            if not errors:
                try:
                    info = await _validate_station(self.hass, station_id)
                except InvalidStation:
                    errors[CONF_STATION_ID] = "invalid_station"
                except RateLimited:
                    errors[CONF_STATION_ID] = "rate_limited"
                except CannotConnect:
                    errors[CONF_STATION_ID] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        subentry,
                        unique_id=station_id,
                        title=info["name"],
                        data_updates={
                            CONF_STATION_ID: station_id,
                            CONF_CRON_EXPRESSION: cron,
                            CONF_PRICE_STALE_HOURS: stale_hours,
                            CONF_TRACKED_FUELS: list(user_input.get(CONF_TRACKED_FUELS) or []),
                            CONF_TRACKED_SERVICE_MODES: list(
                                user_input.get(CONF_TRACKED_SERVICE_MODES) or []
                            ),
                        },
                    )
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_ID, default=current[CONF_STATION_ID]): str,
                vol.Required(
                    CONF_CRON_EXPRESSION,
                    default=current.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION),
                ): str,
                vol.Required(
                    CONF_PRICE_STALE_HOURS,
                    default=str(current.get(CONF_PRICE_STALE_HOURS, DEFAULT_PRICE_STALE_HOURS)),
                ): SelectSelector(
                    SelectSelectorConfig(options=[str(h) for h in PRICE_STALE_HOUR_OPTIONS])
                ),
                vol.Optional(
                    CONF_TRACKED_FUELS, default=current.get(CONF_TRACKED_FUELS, [])
                ): SelectSelector(
                    SelectSelectorConfig(options=[], multiple=True, custom_value=True)
                ),
                vol.Optional(
                    CONF_TRACKED_SERVICE_MODES, default=current.get(CONF_TRACKED_SERVICE_MODES, [])
                ): SelectSelector(SelectSelectorConfig(options=["self", "servito"], multiple=True)),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

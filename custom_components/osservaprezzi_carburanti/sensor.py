from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from math import isfinite
from statistics import mean
from typing import Any, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_FUEL_TYPE_NAME,
    ATTR_IS_SELF,
    ATTR_LAST_UPDATE,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_PREVIOUS_PRICE,
    ATTR_PRICE_CHANGED_AT,
    ATTR_STATION_ADDRESS,
    ATTR_STATION_BRAND,
    ATTR_STATION_NAME,
    ATTR_VALIDITY_DATE,
    CONF_PRICE_STALE_HOURS,
    CONF_STATION_ID,
    CONF_TRACKED_FUELS,
    CONF_TRACKED_SERVICE_MODES,
    DEFAULT_PRICE_STALE_HOURS,
    DOMAIN,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .data_helpers import fuel_display_name, station_display_name
from .entity import (
    OsservaprezziBaseEntity,
    ScheduleAwareEntity,
    _has_valid_opening_hours,
)
from .price_metadata import price_history_metadata
from .price_metadata import price_metadata as _price_metadata

PARALLEL_UPDATES = 0

INFO_SENSOR_DESCRIPTORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="name",
        translation_key="station_name",
        icon="mdi:gas-station",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="company",
        translation_key="station_company",
        icon="mdi:office-building",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="phoneNumber",
        translation_key="station_phone",
        icon="mdi:phone",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="email",
        translation_key="station_email",
        icon="mdi:email",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="website",
        translation_key="station_website",
        icon="mdi:web",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


def _get_fuel_icon(fuel_name: str) -> str:
    """Return the icon for the fuel type."""
    fuel_name_lower = fuel_name.lower()
    if "benzina" in fuel_name_lower:
        return "mdi:gas-station"
    if "gasolio" in fuel_name_lower or "diesel" in fuel_name_lower:
        return "mdi:fuel"
    if "gpl" in fuel_name_lower:
        return "mdi:gas-cylinder"
    if "metano" in fuel_name_lower:
        return "mdi:molecule-co2"
    return "mdi:currency-eur"


MARKET_SUMMARY_FUELS = (
    ("Benzina", "benzina"),
    ("Gasolio", "gasolio"),
    ("GPL", "gpl"),
    ("Metano", "metano"),
)


class MarketSummaryState:
    """Keep summary entities and their station coordinators."""

    def __init__(self, coordinators: list[CarburantiDataUpdateCoordinator]) -> None:
        self.coordinators = coordinators
        self.entities: list[MarketSummarySensor] = []

    @callback
    def refresh(self) -> None:
        """Refresh attached summary entities."""
        for entity in self.entities:
            if entity.hass is not None:
                entity.async_write_ha_state()


class MarketSummarySensor(SensorEntity):
    """Base sensor for prices across configured stations."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _kind: str
    _attr_suggested_display_precision = 3
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "market_summary")},
        name="Osservaprezzi Market Summary",
        manufacturer="MIMIT",
        model="Osservaprezzi Carburanti",
    )

    def __init__(
        self, hass: HomeAssistant, state: MarketSummaryState, fuel: str, slug: str
    ) -> None:
        self._market_hass = hass
        self._market_state = state
        self._fuel = fuel.lower()
        self._attr_native_unit_of_measurement = "€/kg" if slug == "metano" else "€/L"
        self._attr_unique_id = f"market_{self._kind}_{slug}"
        self._attr_translation_key = self._attr_unique_id

    def _prices(self) -> list[tuple[float, dict[str, Any], str, Any]]:
        prices = []
        for coordinator in self._market_state.coordinators:
            data = getattr(coordinator, "data", None) or {}
            fuels = data.get("fuels", {})
            if not isinstance(fuels, dict):
                continue
            station = data.get("station_info", {}) or {}
            candidates = []
            for key, info in fuels.items():
                if not isinstance(key, str) or "_" not in key or not isinstance(info, dict):
                    continue
                name, mode = key.rsplit("_", 1)
                value = info.get("price")
                if (
                    name.lower() != self._fuel
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not isfinite(value)
                    or value <= 0
                ):
                    continue
                candidates.append((float(value), station, mode, info.get("last_update")))
            if candidates:
                prices.append(min(candidates, key=lambda item: item[0]))
        return prices


class OsservaprezziCheapestFuelSensor(MarketSummarySensor):
    """Cheapest price for a fuel across stations."""

    _kind = "cheapest"

    @property
    def native_value(self) -> StateType:
        """Return the lowest price."""
        prices = self._prices()
        return round(min(item[0] for item in prices), 3) if prices else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return information about the cheapest station."""
        prices = self._prices()
        if not prices:
            return {"stations_compared": 0}
        _, station, mode, updated = min(prices, key=lambda item: item[0])
        return {
            "station_id": station.get("id"),
            "station_name": station_display_name(station),
            "brand": station.get("brand"),
            "service_mode": mode,
            "stations_compared": len(prices),
            "last_update": updated,
        }


class OsservaprezziAverageFuelSensor(MarketSummarySensor):
    """Average price for a fuel across stations."""

    _kind = "average"

    @property
    def native_value(self) -> StateType:
        """Return the average price."""
        prices = self._prices()
        return round(mean(item[0] for item in prices), 3) if prices else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the range and number of stations."""
        prices = self._prices()
        if not prices:
            return {"stations_compared": 0}
        return {
            "stations_compared": len(prices),
            "min_price": min(item[0] for item in prices),
            "max_price": max(item[0] for item in prices),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up station sensors and hub-wide market summaries."""

    def setup_station(subentry_id: str, coordinator: CarburantiDataUpdateCoordinator) -> None:
        station_entry = coordinator.config_entry
        station_id = station_entry.data[CONF_STATION_ID]
        known_unique_ids: set[str] = set()
        tracked_fuels = {fuel.lower() for fuel in station_entry.options.get(CONF_TRACKED_FUELS, [])}
        tracked_service_modes = {
            mode.lower() for mode in station_entry.options.get(CONF_TRACKED_SERVICE_MODES, [])
        }

        @callback
        def _async_discover_entities() -> None:
            data = coordinator.data or {}
            entities: list[SensorEntity] = []
            fuels = data.get("fuels", {})
            if isinstance(fuels, dict):
                for fuel_key in fuels:
                    if not isinstance(fuel_key, str) or "_" not in fuel_key:
                        continue
                    if f"{station_id}_{fuel_key}" in known_unique_ids:
                        continue
                    fuel_name, service_mode = fuel_key.rsplit("_", 1)
                    if (
                        tracked_fuels
                        and fuel_name.lower() not in tracked_fuels
                        and fuel_display_name(fuel_name).lower() not in tracked_fuels
                    ):
                        continue
                    if tracked_service_modes and service_mode.lower() not in tracked_service_modes:
                        continue
                    entities.append(
                        OsservaprezziStationSensor(coordinator, station_entry, fuel_key)
                    )
            station_info = data.get("station_info", {})
            if isinstance(station_info, dict):
                for description in INFO_SENSOR_DESCRIPTORS:
                    info_key = description.key
                    if (
                        station_info.get(info_key)
                        and f"{station_id}_{info_key}" not in known_unique_ids
                    ):
                        entities.append(StationInfoSensor(coordinator, station_entry, description))
            if f"{station_id}_location" not in known_unique_ids:
                entities.append(StationLocationSensor(coordinator, station_entry))
            if (
                _has_valid_opening_hours(data)
                and f"{station_id}_next_change" not in known_unique_ids
            ):
                entities.append(StationNextChangeSensor(coordinator, station_entry))
            if not entities:
                return
            known_unique_ids.update(
                entity._attr_unique_id for entity in entities if entity._attr_unique_id is not None
            )
            async_add_entities(entities, update_before_add=False, config_subentry_id=subentry_id)

        _async_discover_entities()
        entry.async_on_unload(coordinator.async_add_listener(_async_discover_entities))

    coordinators = []
    for subentry_id, station in entry.runtime_data.stations.items():
        coordinators.append(station.coordinator)
        setup_station(subentry_id, station.coordinator)
    market = MarketSummaryState(coordinators)
    market.entities = [
        sensor(hass, market, fuel, slug)
        for fuel, slug in MARKET_SUMMARY_FUELS
        for sensor in (OsservaprezziCheapestFuelSensor, OsservaprezziAverageFuelSensor)
    ]
    async_add_entities(market.entities)
    for coordinator in coordinators:
        entry.async_on_unload(coordinator.async_add_listener(market.refresh))


class OsservaprezziStationSensor(OsservaprezziBaseEntity, SensorEntity):
    """Representation of a single fuel price for a specific station."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset(
        {
            "min_30d",
            "max_30d",
            "avg_30d",
            "vs_avg_30d_pct",
            "is_30d_low",
            "price_age_minutes",
            ATTR_FUEL_TYPE_NAME,
            ATTR_IS_SELF,
            ATTR_STATION_NAME,
            ATTR_STATION_ADDRESS,
            ATTR_STATION_BRAND,
        }
    )
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "€/L"

    def __init__(
        self,
        coordinator: CarburantiDataUpdateCoordinator,
        entry: ConfigEntry,
        fuel_key: str,
    ) -> None:
        """Initialize the station fuel sensor."""
        super().__init__(coordinator, entry)
        self._stale_listener: Callable[[], None] | None = None
        self._fuel_key = fuel_key

        self._fuel_name, self._service_type = fuel_key.rsplit("_", 1)
        self._fuel_display_name = fuel_display_name(self._fuel_name)
        self._is_self_service = self._service_type == "self"
        self._attr_name = f"{self._fuel_display_name} {self._service_type.title()}"
        self._attr_unique_id = f"{self._station_id}_{fuel_key}"
        self._attr_icon = _get_fuel_icon(self._fuel_name)
        self._attr_suggested_display_precision = 3

    def _stale_at(self) -> datetime | None:
        """Return the staleness deadline for the current price."""
        fuel: Any = (self.coordinator.data or {}).get("fuels", {}).get(self._fuel_key, {})
        value = fuel.get("last_update")
        parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            return None
        hours = int(
            getattr(self._entry, "options", {}).get(
                CONF_PRICE_STALE_HOURS, DEFAULT_PRICE_STALE_HOURS
            )
        )
        return parsed + timedelta(hours=hours)

    @callback
    def _schedule_stale(self) -> None:
        if self._stale_listener:
            self._stale_listener()
            self._stale_listener = None
        deadline = self._stale_at()
        if deadline is not None and deadline > dt_util.utcnow():
            self._stale_listener = async_track_point_in_utc_time(
                self.hass, self._stale_tick, dt_util.as_utc(deadline)
            )

    @callback
    def _stale_tick(self, now: datetime) -> None:
        self._stale_listener = None
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule_stale()

    async def async_will_remove_from_hass(self) -> None:
        if self._stale_listener:
            self._stale_listener()
            self._stale_listener = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_stale()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> StateType:
        """Return the sensor state."""
        if not self.coordinator.data:
            return None
        fuel: Any = self.coordinator.data["fuels"].get(self._fuel_key, {})
        return cast(StateType, fuel.get("price"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return fuel-specific state attributes."""
        if not self.coordinator.data:
            return {}

        fuel_info = self.coordinator.data.get("fuels", {}).get(self._fuel_key)
        if not fuel_info:
            return {}

        attributes = {
            ATTR_FUEL_TYPE_NAME: self._fuel_display_name,
            ATTR_IS_SELF: self._is_self_service,
            ATTR_LAST_UPDATE: fuel_info.get("last_update"),
            ATTR_VALIDITY_DATE: fuel_info.get("validity_date"),
            ATTR_STATION_NAME: station_display_name(self.station_info, self._station_id),
            ATTR_STATION_ADDRESS: self.station_info.get("address"),
            ATTR_STATION_BRAND: self.station_info.get("brand"),
            ATTR_PREVIOUS_PRICE: fuel_info.get("previous_price"),
            ATTR_PRICE_CHANGED_AT: fuel_info.get("price_changed_at"),
        }
        attributes.update(
            _price_metadata(
                price=fuel_info.get("price"),
                previous_price=fuel_info.get("previous_price"),
                last_update=fuel_info.get("last_update"),
                stale_hours=int(
                    getattr(self._entry, "options", {}).get(
                        CONF_PRICE_STALE_HOURS,
                        DEFAULT_PRICE_STALE_HOURS,
                    )
                ),
            )
        )
        attributes.update(
            price_history_metadata(fuel_info.get("price"), fuel_info.get("price_history"))
        )
        return attributes

    @property
    def available(self) -> bool:
        """Return whether this fuel is available from the coordinator."""
        return bool(
            super().available
            and self.coordinator.data
            and self._fuel_key in self.coordinator.data.get("fuels", {})
        )


class StationInfoSensor(OsservaprezziBaseEntity, SensorEntity):
    """Representation of a sensor for station information."""

    _attr_entity_registry_enabled_default = False

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarburantiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the info sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._info_key = description.key
        self._attr_unique_id = f"{self._station_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the station info value."""
        return self.station_info.get(self._info_key)


class StationLocationSensor(OsservaprezziBaseEntity, SensorEntity):
    """Representation of a sensor for station location."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize location sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "location"
        self._attr_unique_id = f"{self._station_id}_location"

    @property
    def native_value(self) -> StateType:
        """Return the station address for map cards and diagnostics."""
        return self.station_info.get("address") or station_display_name(
            self.station_info, self._station_id
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes including coordinates for map cards."""
        return {
            ATTR_STATION_NAME: station_display_name(self.station_info, self._station_id),
            ATTR_STATION_ADDRESS: self.station_info.get("address"),
            ATTR_STATION_BRAND: self.station_info.get("brand"),
            ATTR_LATITUDE: self.station_info.get(ATTR_LATITUDE),
            ATTR_LONGITUDE: self.station_info.get(ATTR_LONGITUDE),
            "municipality": self.station_info.get("municipality"),
            "province": self.station_info.get("province"),
            "station_type": self.station_info.get("station_type"),
            "operator": self.station_info.get("operator"),
            "coordinate_source": self.station_info.get("coordinate_source"),
        }

    @property
    def available(self) -> bool:
        """Return True if coordinates are available."""
        return (
            super().available
            and self.station_info.get(ATTR_LATITUDE) is not None
            and self.station_info.get(ATTR_LONGITUDE) is not None
        )


class StationNextChangeSensor(ScheduleAwareEntity, SensorEntity):
    """Sensor indicating when the station will next open or close."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"minutes_until_change"})

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the next-change sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "next_change"
        self._attr_unique_id = f"{self._station_id}_next_change"
        self._next_change: tuple[str, datetime | None] = ("no_schedule", None)
        self._next_change_now: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Prime the cached schedule result after Home Assistant attaches the entity."""
        self._refresh_next_change()
        await super().async_added_to_hass()

    @callback
    def _next_transition_time(self, now: datetime | None = None) -> datetime | None:
        """Reuse the cached next transition datetime instead of recomputing."""
        return self._next_change[1]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the schedule result once for each coordinator update."""
        self._refresh_next_change()
        super()._handle_coordinator_update()

    @callback
    def _handle_time_tick(self, now: datetime) -> None:
        """Recompute the schedule result once for each clock tick."""
        self._refresh_next_change(dt_util.as_local(now))
        super()._handle_time_tick(now)

    def _refresh_next_change(self, now: datetime | None = None) -> None:
        """Cache the next schedule transition used by all entity properties."""
        self._next_change_now = now or dt_util.now()
        self._next_change = self._compute_next_change(self._next_change_now)

    @property
    def native_value(self) -> datetime | None:
        """Return the next change description."""
        _, change_time = self._next_change
        return dt_util.as_utc(change_time) if change_time is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        change_type, change_time = self._next_change
        attributes: dict[str, Any] = {
            "change_type": change_type,
            "next_change_time": change_time.isoformat() if change_time else None,
        }
        if change_time:
            now = dt_util.now()
            attributes["minutes_until_change"] = int((change_time - now).total_seconds() / 60)
        return attributes

    @property
    def available(self) -> bool:
        """Return True if schedule data is available."""
        return bool(super().available and _has_valid_opening_hours(self.coordinator.data))

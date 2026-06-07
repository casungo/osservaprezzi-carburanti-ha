"""Shared test fixtures and mock helpers."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


class _MockEntity:
    """Minimal Home Assistant entity test double."""

    hass = None

    async def async_added_to_hass(self):
        """Mock add hook."""

    async def async_will_remove_from_hass(self):
        """Mock remove hook."""


class _MockSensorEntity(_MockEntity):
    """Minimal sensor entity test double."""


class _MockBinarySensorEntity(_MockEntity):
    """Minimal binary sensor entity test double."""


class _MockCoordinatorEntity(_MockEntity):
    """Minimal coordinator entity test double."""

    def __init__(self, coordinator=None, *args, **kwargs):
        """Store coordinator and hass like Home Assistant's CoordinatorEntity."""
        self.coordinator = coordinator
        self.hass = coordinator if args or kwargs else getattr(coordinator, "hass", None)
        self.data = None

    async def async_shutdown(self):
        """Mock shutdown hook."""


class _MockConfigFlow:
    """Minimal config flow test double."""

    def __init_subclass__(cls, **kwargs):
        """Accept Home Assistant's domain keyword during subclassing."""
        super().__init_subclass__()


class _MockOptionsFlow:
    """Minimal options flow test double."""

    def __init__(self, config_entry=None):
        """Store the config entry like Home Assistant's options flow base."""
        self.config_entry = config_entry
        self.options = getattr(config_entry, "options", {}) if config_entry else {}


def _mock_ha_modules():
    ha_modules = [
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.sensor",
        "homeassistant.components.binary_sensor",
        "homeassistant.components.geo_location",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.config_validation",
        "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.helpers.typing",
        "homeassistant.helpers.aiohttp_client",
        "homeassistant.helpers.event",
        "homeassistant.util",
        "homeassistant.util.dt",
        "homeassistant.const",
        "homeassistant.exceptions",
        "homeassistant.data_entry_flow",
        "voluptuous",
    ]
    for mod_name in ha_modules:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()

    sys.modules["homeassistant.components.sensor"].SensorEntity = _MockSensorEntity
    sys.modules["homeassistant.components.sensor"].SensorStateClass = MagicMock(
        MEASUREMENT="measurement"
    )
    sys.modules["homeassistant.components.binary_sensor"].BinarySensorEntity = _MockBinarySensorEntity
    sys.modules["homeassistant.components.geo_location"].GeolocationEvent = _MockEntity
    sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = _MockCoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = _MockCoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = Exception
    sys.modules["homeassistant.helpers.entity"].DeviceInfo = dict
    sys.modules["homeassistant.helpers.entity"].EntityCategory = MagicMock(
        DIAGNOSTIC="diagnostic"
    )
    sys.modules["homeassistant.helpers.typing"].StateType = object
    sys.modules["homeassistant.const"].Platform = MagicMock(
        SENSOR="sensor",
        BINARY_SENSOR="binary_sensor",
        GEO_LOCATION="geo_location",
    )
    sys.modules["homeassistant.exceptions"].HomeAssistantError = Exception
    sys.modules["homeassistant.exceptions"].ConfigEntryNotReady = Exception
    sys.modules["homeassistant.data_entry_flow"].FlowResult = dict
    sys.modules["homeassistant.config_entries"].ConfigFlow = _MockConfigFlow
    sys.modules["homeassistant.config_entries"].ConfigFlowResult = dict
    sys.modules["homeassistant.config_entries"].OptionsFlowWithConfigEntry = _MockOptionsFlow
    sys.modules["homeassistant.core"].callback = lambda f: f

    sys.modules["homeassistant"].config_entries = sys.modules["homeassistant.config_entries"]
    sys.modules["homeassistant"].core = sys.modules["homeassistant.core"]
    sys.modules["homeassistant"].exceptions = sys.modules["homeassistant.exceptions"]
    sys.modules["homeassistant"].const = sys.modules["homeassistant.const"]
    sys.modules["homeassistant"].util = sys.modules["homeassistant.util"]
    sys.modules["homeassistant.util"].dt = sys.modules["homeassistant.util.dt"]
    sys.modules["homeassistant"].helpers = sys.modules["homeassistant.helpers"]
    sys.modules["homeassistant.helpers"].entity_registry = sys.modules[
        "homeassistant.helpers.entity_registry"
    ]
    sys.modules["homeassistant.helpers"].aiohttp_client = sys.modules[
        "homeassistant.helpers.aiohttp_client"
    ]
    sys.modules["homeassistant.helpers"].config_validation = sys.modules[
        "homeassistant.helpers.config_validation"
    ]
    sys.modules["homeassistant.helpers.config_validation"].empty_config_schema = (
        lambda domain: MagicMock()
    )
    sys.modules["homeassistant.helpers"].event = sys.modules["homeassistant.helpers.event"]
    sys.modules["homeassistant.helpers"].update_coordinator = sys.modules[
        "homeassistant.helpers.update_coordinator"
    ]


_mock_ha_modules()

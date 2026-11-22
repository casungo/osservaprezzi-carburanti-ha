from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .coordinator import CarburantiDataUpdateCoordinator


@dataclass
class StationRuntimeData:
    """Resources owned by a station subentry."""

    subentry_id: str
    station_id: str
    coordinator: CarburantiDataUpdateCoordinator
    listener: Callable[[], None] | None = None
    coordinator_listener: Callable[[], None] | None = None
    initial_refresh_task: asyncio.Task[None] | None = None


@dataclass
class OsservaprezziRuntimeData:
    """Resources owned by a loaded config entry."""

    stations: dict[str, StationRuntimeData]


type OsservaprezziConfigEntry = ConfigEntry[OsservaprezziRuntimeData]

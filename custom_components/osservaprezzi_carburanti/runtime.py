"""Typed runtime state for a configured station."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import CarburantiDataUpdateCoordinator


@dataclass
class OsservaprezziRuntimeData:
    """Resources owned by one configuration entry."""

    coordinator: CarburantiDataUpdateCoordinator
    listener: Callable[[], None] | None = None
    initial_refresh_task: asyncio.Task[None] | None = None
    initial_refresh_stop_event: asyncio.Event | None = None
    refresh_result_listener: Callable[[], None] | None = None


type OsservaprezziConfigEntry = ConfigEntry[OsservaprezziRuntimeData]

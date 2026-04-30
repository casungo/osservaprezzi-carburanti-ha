from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from cronsim import CronSim, CronSimError
else:
    try:
        from cronsim import CronSim, CronSimError
    except ImportError:
        CronSim = None  # type: ignore[assignment,misc]
        CronSimError = ValueError  # type: ignore[assignment]


def validate_cron_expression(cron_expr: str) -> bool:
    """Validate a cron expression using CronSim."""
    if CronSim is None:
        return False
    try:
        CronSim(cron_expr, dt_util.now())
        return True
    except (CronSimError, TypeError, ValueError):
        return False


def get_next_run_time(cron_expr: str, base_time: datetime | None = None) -> datetime:
    """Get the next run time for a cron expression."""
    if CronSim is None:
        raise ImportError("cronsim is required for cron scheduling")

    if base_time is None:
        base_time = dt_util.now()

    cron = CronSim(cron_expr, base_time)
    return next(cron)

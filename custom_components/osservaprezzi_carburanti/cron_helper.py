from __future__ import annotations
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from cronsim import CronSim
else:
    try:
        from cronsim import CronSim
    except ImportError:
        CronSim = None  # type: ignore[assignment,misc]

def validate_cron_expression(cron_expr: str) -> bool:
    """Validate a cron expression using CronSim."""
    if CronSim is None:
        return False
    try:
        CronSim(cron_expr, dt_util.now())
        return True
    except Exception:
        return False

def get_next_run_time(cron_expr: str, base_time: Optional[datetime] = None) -> datetime:
    """Get the next run time for a cron expression."""
    if CronSim is None:
        raise ImportError("cronsim is required for cron scheduling")

    if base_time is None:
        base_time = dt_util.now()

    cron = CronSim(cron_expr, base_time)
    return next(cron)

def get_schedule_interval(cron_expr: str) -> timedelta:
    """Calculate the interval until the next run time."""
    next_run = get_next_run_time(cron_expr)
    now = dt_util.now()
    return next_run - now
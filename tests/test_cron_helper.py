"""Tests for cron helper functions."""
from __future__ import annotations
import sys
from datetime import datetime, timezone

import pytest


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.cron_helper import validate_cron_expression

try:
    from cronsim import CronSim
except ImportError:
    CronSim = None


@pytest.mark.skipif(CronSim is None, reason="cronsim not installed")
class TestCronHelper:
    def test_validate_valid_expression(self):
        assert validate_cron_expression("30 8 * * *") is True

    def test_validate_valid_every_hour(self):
        assert validate_cron_expression("0 * * * *") is True

    def test_validate_valid_specific_days(self):
        assert validate_cron_expression("30 7 * * 1-5") is True

    def test_validate_invalid_expression(self):
        assert validate_cron_expression("not valid") is False

    def test_validate_empty_string(self):
        assert validate_cron_expression("") is False

    def test_get_next_run_time(self):
        from custom_components.osservaprezzi_carburanti.cron_helper import get_next_run_time
        base = datetime(2025, 3, 15, 10, 0, tzinfo=timezone.utc)
        next_run = get_next_run_time("30 8 * * *", base)
        assert next_run.hour == 8
        assert next_run.minute == 30

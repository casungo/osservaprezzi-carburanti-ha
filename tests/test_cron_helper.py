"""Tests for cron helper functions."""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone

import pytest


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.cron_helper import validate_cron_expression
from custom_components.osservaprezzi_carburanti import cron_helper

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


class _FakeCronError(Exception):
    """Fake cronsim validation error."""


class _FakeCronSim:
    """Minimal CronSim replacement for environments without cronsim."""

    def __init__(self, expression: str, base_time: datetime) -> None:
        if expression == "bad":
            raise _FakeCronError("bad expression")
        self.base_time = base_time

    def __iter__(self) -> "_FakeCronSim":
        return self

    def __next__(self) -> datetime:
        return self.base_time + timedelta(hours=1)


def test_validate_cron_expression_with_fake_cronsim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cron_helper, "CronSim", _FakeCronSim)
    monkeypatch.setattr(cron_helper, "CronSimError", _FakeCronError)
    monkeypatch.setattr(cron_helper.dt_util, "now", lambda: datetime(2025, 1, 1, tzinfo=timezone.utc))

    assert cron_helper.validate_cron_expression("0 * * * *") is True
    assert cron_helper.validate_cron_expression("bad") is False


def test_validate_cron_expression_without_cronsim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cron_helper, "CronSim", None)

    assert cron_helper.validate_cron_expression("0 * * * *") is False


def test_get_next_run_time_with_fake_cronsim(monkeypatch: pytest.MonkeyPatch) -> None:
    base = datetime(2025, 1, 1, 8, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(cron_helper, "CronSim", _FakeCronSim)

    assert cron_helper.get_next_run_time("0 * * * *", base) == base + timedelta(hours=1)


def test_get_next_run_time_uses_current_time(monkeypatch: pytest.MonkeyPatch) -> None:
    base = datetime(2025, 1, 1, 8, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(cron_helper, "CronSim", _FakeCronSim)
    monkeypatch.setattr(cron_helper.dt_util, "now", lambda: base)

    assert cron_helper.get_next_run_time("0 * * * *") == base + timedelta(hours=1)


def test_get_next_run_time_without_cronsim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cron_helper, "CronSim", None)

    with pytest.raises(ImportError, match="cronsim is required"):
        cron_helper.get_next_run_time("0 * * * *")

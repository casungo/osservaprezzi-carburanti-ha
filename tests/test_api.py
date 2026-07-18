"""Tests for Osservaprezzi API helpers."""
from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti import api  # noqa: E402
from custom_components.osservaprezzi_carburanti.api import fetch_station_data  # noqa: E402


class _ResponseContext:
    """Async context manager returning a fake aiohttp response."""

    def __init__(self, response: "_FakeResponse") -> None:
        self.response = response

    async def __aenter__(self) -> "_FakeResponse":
        return self.response

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _FakeResponse:
    """Small response double with the attributes used by fetch_station_data."""

    def __init__(
        self,
        status: int,
        payload: Any = None,
        reason: str = "reason",
    ) -> None:
        self.status = status
        self._payload = payload or {}
        self.reason = reason
        self.request_info = None
        self.history = ()
        self.headers = {"X-Test": "yes"}

    async def json(self) -> Any:
        return self._payload


class _FakeSession:
    """Session double returning a configured response or raising an error."""

    def __init__(self, response: _FakeResponse | BaseException) -> None:
        self.response = response
        self.get_calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _ResponseContext:
        self.get_calls.append({"url": url, **kwargs})
        if isinstance(self.response, BaseException):
            raise self.response
        return _ResponseContext(self.response)


@pytest.fixture(autouse=True)
def reset_request_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable inter-test sleeps from the module-level API throttle."""
    monkeypatch.setattr(api, "_NEXT_ALLOWED_REQUEST_AT", 0.0)
    monkeypatch.setattr(api, "API_REQUEST_INTERVAL_SECONDS", 0)


def test_fetch_station_data_success(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    payload = {
        "id": "123",
        "name": "Distinctive Secret Station",
        "address": "Private Street 987",
        "latitude": 41.123456,
        "longitude": 12.654321,
        "prices": [{"fuel": "PrivateFuel", "price": 9.876}],
    }
    session = _FakeSession(_FakeResponse(200, payload))
    monkeypatch.setattr(api, "async_get_clientsession", MagicMock(return_value=session))

    with caplog.at_level(logging.DEBUG, logger=api.__name__):
        result = asyncio.run(fetch_station_data(MagicMock(), "123", timeout=7))

    assert result == payload
    assert "/123" in session.get_calls[0]["url"]
    assert session.get_calls[0]["headers"] == api.DEFAULT_HEADERS
    assert isinstance(session.get_calls[0]["timeout"], aiohttp.ClientTimeout)
    assert session.get_calls[0]["timeout"].total == 7
    assert "response for 123" in caplog.text
    assert "status=200" in caplog.text
    assert "field_count=6" in caplog.text
    assert "collection_counts={'lists': 1, 'mappings': 0}" in caplog.text
    for private_value in (
        "Distinctive Secret Station",
        "Private Street 987",
        "41.123456",
        "12.654321",
        "PrivateFuel",
        "9.876",
    ):
        assert private_value not in caplog.text


def test_fetch_station_data_does_not_log_malformed_payload(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    payload = ["Distinctive malformed station data"]
    session = _FakeSession(_FakeResponse(200, payload))
    monkeypatch.setattr(api, "async_get_clientsession", MagicMock(return_value=session))

    with caplog.at_level(logging.DEBUG, logger=api.__name__):
        result = asyncio.run(fetch_station_data(MagicMock(), "456"))

    assert result == payload
    assert "response for 456" in caplog.text
    assert "payload_type=list" in caplog.text
    assert "Distinctive malformed station data" not in caplog.text


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (404, "not found"),
        (429, "Rate limit exceeded"),
        (500, "Service error: 500"),
    ],
)
def test_fetch_station_data_raises_response_errors(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    message: str,
) -> None:
    session = _FakeSession(_FakeResponse(status))
    monkeypatch.setattr(api, "async_get_clientsession", MagicMock(return_value=session))

    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        asyncio.run(fetch_station_data(MagicMock(), "123"))

    assert exc_info.value.status == status
    assert message in exc_info.value.message


def test_fetch_station_data_reraises_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api,
        "async_get_clientsession",
        MagicMock(return_value=_FakeSession(aiohttp.ClientError("boom"))),
    )

    with pytest.raises(aiohttp.ClientError, match="boom"):
        asyncio.run(fetch_station_data(MagicMock(), "123"))


def test_wait_for_request_slot_sleeps_when_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monotonic_values = iter([10.0, 20.0])

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    def fake_monotonic() -> float:
        return next(monotonic_values, 20.0)

    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=fake_monotonic))
    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api, "_NEXT_ALLOWED_REQUEST_AT", 12.5)
    monkeypatch.setattr(api, "API_REQUEST_INTERVAL_SECONDS", 4)

    asyncio.run(api._wait_for_request_slot("123"))

    assert slept == [2.5]
    assert api._NEXT_ALLOWED_REQUEST_AT == 24.0

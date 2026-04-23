"""Tests for coordinator retry helpers."""
from __future__ import annotations

import asyncio
import sys
from typing import Any, cast

import aiohttp


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.coordinator import (
    CarburantiDataUpdateCoordinator,
)


def _make_response_error(
    status: int,
    headers: dict[str, str] | None = None,
) -> aiohttp.ClientResponseError:
    """Create a minimal response error for tests."""
    return aiohttp.ClientResponseError(
        request_info=cast(Any, None),
        history=(),
        status=status,
        message="test",
        headers=cast(Any, headers),
    )


class TestRetryDelay:
    def test_uses_retry_after_header(self) -> None:
        err = _make_response_error(429, {"Retry-After": "15"})
        assert CarburantiDataUpdateCoordinator._get_retry_delay(err, 30) == 15

    def test_ignores_invalid_retry_after_header(self) -> None:
        err = _make_response_error(429, {"Retry-After": "invalid"})
        assert CarburantiDataUpdateCoordinator._get_retry_delay(err, 30) == 30

    def test_ignores_non_positive_retry_after_header(self) -> None:
        err = _make_response_error(429, {"Retry-After": "0"})
        assert CarburantiDataUpdateCoordinator._get_retry_delay(err, 30) == 30


class TestTransientErrors:
    def test_timeout_is_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(asyncio.TimeoutError()) is True

    def test_404_is_not_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(_make_response_error(404)) is False

    def test_429_is_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(_make_response_error(429)) is True

    def test_client_error_is_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(aiohttp.ClientError()) is True

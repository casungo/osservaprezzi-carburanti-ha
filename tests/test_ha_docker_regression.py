"""Tests for bounded Home Assistant Docker regression logs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def regression_script() -> ModuleType:
    """Load the regression script without invoking Docker or its CLI."""
    script_path = Path(__file__).parents[1] / "scripts" / "ha_docker_regression.py"
    spec = importlib.util.spec_from_file_location("ha_docker_regression", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("logs", "limit", "expected"),
    [
        ("", 3, ""),
        ("one\ntwo", 3, "one\ntwo"),
        ("one\ntwo\nthree", 3, "one\ntwo\nthree"),
        ("one\ntwo\nthree\nfour", 3, "two\nthree\nfour"),
    ],
)
def test_log_tail_is_bounded_and_ordered(
    regression_script: ModuleType, logs: str, limit: int, expected: str
) -> None:
    assert regression_script._log_tail(logs, limit) == expected


def test_log_failure_context_preserves_stage_and_return_code(
    regression_script: ModuleType,
) -> None:
    logs = "\n".join(f"line-{index}" for index in range(100))

    result = regression_script._log_failure_context(
        "container startup", logs, "test-container", return_code=17
    )

    assert "Failure stage: container startup" in result
    assert "Return code: 17" in result
    assert "line-19" not in result
    assert "line-20\nline-21" in result
    assert result.index("line-20") < result.index("line-99")
    assert "docker logs test-container" in result

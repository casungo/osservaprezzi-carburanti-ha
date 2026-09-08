"""Tests for bounded Home Assistant Docker regression logs."""
from __future__ import annotations

import importlib.util
import sqlite3
import time
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


def test_copy_integration_excludes_bytecode(
    regression_script: ModuleType,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    source = repo_root / "custom_components" / regression_script.DOMAIN
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    bytecode = source / "__pycache__"
    bytecode.mkdir()
    (bytecode / "module.pyc").write_bytes(b"cached")

    config_dir = tmp_path / "config"
    regression_script._copy_integration(repo_root, config_dir)

    copied = config_dir / "custom_components" / regression_script.DOMAIN
    assert (copied / "__init__.py").is_file()
    assert not (copied / "__pycache__").exists()


def test_docker_config_declares_profile_and_station_ids(
    regression_script: ModuleType,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    regression_script._write_ha_config(config_dir, "lived", ["54233", "54234"])

    configuration = (config_dir / "configuration.yaml").read_text(encoding="utf-8")
    assert "ha_docker_probe:" in configuration
    assert "profile: lived" in configuration
    assert "    - '54233'" in configuration
    assert "    - '54234'" in configuration


def test_lived_profile_requires_persisted_home_assistant_data(
    regression_script: ModuleType,
    tmp_path: Path,
) -> None:
    storage = tmp_path / ".storage"
    storage.mkdir()
    (storage / "core.config_entries").write_text(
        '{"data": {"entries": [{"domain": "osservaprezzi_carburanti"}]}}',
        encoding="utf-8",
    )
    (storage / "core.entity_registry").write_text(
        '{"data": {"entities": [{"platform": "osservaprezzi_carburanti"}]}}',
        encoding="utf-8",
    )
    (storage / "osservaprezzi_carburanti_cache.json").write_text(
        '{"stations": {"54233": {}}}',
        encoding="utf-8",
    )

    assert regression_script._assert_persisted_profile(tmp_path) == {
        "config_entries": 1,
        "entity_registry_entries": 1,
        "cached_stations": 1,
    }


def test_lived_profile_requires_multi_day_recorder_history(
    regression_script: ModuleType,
    tmp_path: Path,
) -> None:
    (tmp_path / "lived-profile-builder.json").write_text(
        '{"boot_count": 4}', encoding="utf-8"
    )
    database_path = tmp_path / "home-assistant_v2.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE states (last_updated_ts REAL)")
        connection.executemany(
            "INSERT INTO states VALUES (?)",
            [(time.time() - offset * 86400,) for offset in range(14)],
        )

    summary = regression_script._assert_aged_profile(tmp_path)

    assert summary["boots"] == 4
    assert summary["history_days"] == 14
    assert summary["history_span_days"] == 13.0

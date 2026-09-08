"""Run Home Assistant Docker smoke regressions for the custom integration."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path


DOMAIN = "osservaprezzi_carburanti"
DEFAULT_IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
DEFAULT_STATION_IDS = ("54233", "54234", "54404", "54235")
STARTUP_OK_PATTERN = re.compile(
    rf"Home Assistant initialized|Starting Home Assistant|custom integration {DOMAIN}",
    re.IGNORECASE,
)
ERROR_PATTERNS = (
    re.compile(rf"Setup failed for custom integration '?{DOMAIN}'?", re.IGNORECASE),
    re.compile(rf"Error setting up .*{DOMAIN}", re.IGNORECASE),
    re.compile(rf"Integration '?{DOMAIN}'? not found", re.IGNORECASE),
    re.compile(r"Failed to load integration", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE),
)
LOG_TAIL_LINE_LIMIT = 80
LIVED_HISTORY_DAYS = 14
LIVED_BOOT_COUNT = 4
UPGRADE_FROM_TAG = "v2.6.0-beta.1"


def _log_tail(logs: str, line_limit: int = LOG_TAIL_LINE_LIMIT) -> str:
    """Return at most the last configured number of log lines."""
    return "\n".join(logs.splitlines()[-line_limit:])


def _log_failure_context(
    stage: str,
    logs: str,
    container_name: str,
    *,
    return_code: int | None = None,
) -> str:
    """Format bounded logs while preserving failure-stage context."""
    return_code_context = "" if return_code is None else f"\nReturn code: {return_code}"
    return (
        f"Failure stage: {stage}{return_code_context}\n"
        f"Last {LOG_TAIL_LINE_LIMIT} log lines:\n{_log_tail(logs)}\n"
        f"Full container log: docker logs {container_name}"
    )


def _run(
    command: list[str],
    *,
    timeout: int = 30,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and return captured output."""
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            f"{' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def _write_ha_config(config_dir: Path, profile: str, station_ids: list[str]) -> None:
    """Write a minimal Home Assistant config that loads the custom integration."""
    (config_dir / "configuration.yaml").write_text(
        "\n".join(
            [
                "homeassistant:",
                "  name: Docker Regression",
                "  latitude: 41.9028",
                "  longitude: 12.4964",
                "  elevation: 21",
                "  unit_system: metric",
                "  time_zone: Europe/Rome",
                "",
                "logger:",
                "  default: warning",
                "  logs:",
                f"    custom_components.{DOMAIN}: info",
                "",
                "recorder:",
                "  purge_keep_days: 30",
                "",
                f"{DOMAIN}:",
                "",
                "ha_docker_probe:",
                f"  profile: {profile}",
                "  station_ids:",
                *[f"    - '{station_id}'" for station_id in station_ids],
                "",
            ]
        ),
        encoding="utf-8",
    )


def _copy_probe(repo_root: Path, config_dir: Path) -> None:
    """Copy the test-only probe that runs inside Home Assistant's process."""
    source = repo_root / "scripts" / "ha_docker_probe.py"
    destination = config_dir / "custom_components" / "ha_docker_probe"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination / "__init__.py")
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "ha_docker_probe",
                "name": "HA Docker Probe",
                "version": "1.0.0",
                "integration_type": "helper",
                "documentation": "https://github.com/casungo/osservaprezzi-carburanti-ha",
                "requirements": [],
            }
        ),
        encoding="utf-8",
    )


def _copy_integration(repo_root: Path, config_dir: Path) -> None:
    """Copy the integration into Home Assistant's config directory."""
    source = repo_root / "custom_components" / DOMAIN
    destination = config_dir / "custom_components" / DOMAIN
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _extract_tagged_integration(repo_root: Path, destination: Path, tag: str) -> Path:
    """Extract one tagged integration into a temporary source tree."""
    archive_path = destination / "previous-integration.tar"
    destination.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as archive_file:
        result = subprocess.run(
            ["git", "archive", "--format=tar", tag, f"custom_components/{DOMAIN}"],
            cwd=repo_root,
            stdout=archive_file,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not extract {tag}: {result.stderr.decode(errors='replace')}"
        )
    with tarfile.open(archive_path) as archive:
        archive.extractall(destination)
    source = destination / "custom_components" / DOMAIN
    if not source.is_dir():
        raise AssertionError(f"Tagged integration was not extracted: {source}")
    return destination


def _replace_integration(repo_root: Path, config_dir: Path) -> None:
    """Replace only the integration code in an exported HA profile."""
    destination = config_dir / "custom_components" / DOMAIN
    shutil.rmtree(destination)
    _copy_integration(repo_root, config_dir)


def _integration_version(repo_root: Path) -> str:
    """Read the integration version from a source tree."""
    manifest_path = repo_root / "custom_components" / DOMAIN / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return str(manifest["version"])


def _docker_logs(container_name: str, docker_env: dict[str, str]) -> str:
    """Return container logs."""
    result = _run(["docker", "logs", container_name], check=False, env=docker_env)
    return result.stdout + result.stderr


def _home_assistant_logs(container_name: str, docker_env: dict[str, str]) -> str:
    """Return Home Assistant's log file content when available."""
    result = _run(
        ["docker", "exec", container_name, "sh", "-c", "cat /config/home-assistant.log 2>/dev/null"],
        check=False,
        env=docker_env,
    )
    return result.stdout + result.stderr


def _combined_logs(container_name: str, docker_env: dict[str, str]) -> str:
    """Return all available Home Assistant logs."""
    return _docker_logs(container_name, docker_env) + _home_assistant_logs(container_name, docker_env)


def _assert_no_error_logs(
    logs: str,
    container_name: str,
    *,
    allow_expected_upstream_errors: bool = False,
) -> None:
    """Fail if logs contain integration startup errors."""
    patterns = ERROR_PATTERNS[:-1] if allow_expected_upstream_errors else ERROR_PATTERNS
    for pattern in patterns:
        if pattern.search(logs):
            context = _log_failure_context("integration log check", logs, container_name)
            raise AssertionError(
                f"Home Assistant logs contain an error matching {pattern.pattern}:\n{context}"
            )


def _wait_for_startup(
    container_name: str,
    timeout: int,
    docker_env: dict[str, str],
    *,
    allow_expected_upstream_errors: bool = False,
) -> str:
    """Wait until Home Assistant has started far enough to validate logs."""
    deadline = time.monotonic() + timeout
    last_logs = ""
    while time.monotonic() < deadline:
        inspect = _run(
            ["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", container_name],
            check=False,
            env=docker_env,
        )
        if inspect.returncode != 0:
            raise RuntimeError(inspect.stderr)
        if inspect.stdout.strip().startswith("false"):
            logs = _combined_logs(container_name, docker_env)
            return_code = None
            inspect_parts = inspect.stdout.split()
            if len(inspect_parts) > 1 and inspect_parts[1].isdigit():
                return_code = int(inspect_parts[1])
            context = _log_failure_context(
                "container startup", logs, container_name, return_code=return_code
            )
            raise RuntimeError(f"Home Assistant container exited early:\n{context}")

        last_logs = _combined_logs(container_name, docker_env)
        _assert_no_error_logs(
            last_logs,
            container_name,
            allow_expected_upstream_errors=allow_expected_upstream_errors,
        )
        if DOMAIN in last_logs:
            return last_logs
        time.sleep(3)

    context = _log_failure_context("startup timeout", last_logs, container_name)
    raise TimeoutError(f"Timed out waiting for Home Assistant startup.\n{context}")


def _run_import_contract(container_name: str, docker_env: dict[str, str]) -> None:
    """Verify integration modules import against the real Home Assistant runtime."""
    script = f"""
from __future__ import annotations

import importlib
import sys

sys.path.insert(0, "/config")

modules = [
    "custom_components.{DOMAIN}",
    "custom_components.{DOMAIN}.api",
    "custom_components.{DOMAIN}.config_flow",
    "custom_components.{DOMAIN}.const",
    "custom_components.{DOMAIN}.coordinator",
    "custom_components.{DOMAIN}.cron_helper",
    "custom_components.{DOMAIN}.csv_manager",
    "custom_components.{DOMAIN}.diagnostics",
    "custom_components.{DOMAIN}.discovery",
    "custom_components.{DOMAIN}.sensor",
]

for module_name in modules:
    importlib.import_module(module_name)

from custom_components.{DOMAIN}.const import ADDITIONAL_SERVICES, SERVICE_ID_TO_TRANSLATION_KEY
from custom_components.{DOMAIN}.entity import _get_available_service_ids

missing = set(ADDITIONAL_SERVICES) - set(SERVICE_ID_TO_TRANSLATION_KEY)
if missing:
    raise AssertionError(f"Missing service translation keys: {{sorted(missing)}}")

normalized = _get_available_service_ids([{{"id": 1}}, "2", 3, {{"other": "ignored"}}])
if normalized != {{"1", "2", "3"}}:
    raise AssertionError(f"Unexpected service normalization: {{normalized}}")

print("HA import contract passed")
"""
    _run(["docker", "exec", container_name, "python", "-c", script], timeout=60, env=docker_env)


def _run_discovery_contract(
    container_name: str,
    docker_env: dict[str, str],
) -> dict[str, int | str]:
    """Search the live cached MIMIT registry around the configured test home."""
    script = f"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/config")

from custom_components.{DOMAIN}.discovery import find_nearby_stations

cache_path = Path("/config/.storage/{DOMAIN}_cache.json")
payload = json.loads(cache_path.read_text(encoding="utf-8"))
candidates = find_nearby_stations(
    payload["stations"].values(),
    latitude=41.9028,
    longitude=12.4964,
    radius_km=20,
    limit=20,
)
if not candidates:
    raise AssertionError("Live registry discovery returned no stations around the test home")
if len(candidates) > 20:
    raise AssertionError(f"Discovery result cap was not enforced: {{len(candidates)}}")
if list(candidates) != sorted(
    candidates,
    key=lambda candidate: candidate.distance_km,
):
    raise AssertionError("Discovery results are not ordered by distance")

print(json.dumps({{
    "count": len(candidates),
    "nearest_station_id": candidates[0].station_id,
}}))
"""
    result = _run(
        ["docker", "exec", container_name, "python", "-c", script],
        timeout=60,
        env=docker_env,
    )
    return json.loads(result.stdout)


def _entity_counts(container_name: str, docker_env: dict[str, str], station_ids: list[str]) -> dict[str, int] | None:
    """Return entity counts by station id from Home Assistant's entity registry."""
    script = f"""
from __future__ import annotations

import json
from pathlib import Path

path = Path("/config/.storage/core.entity_registry")
if not path.exists():
    raise SystemExit(2)

payload = json.loads(path.read_text(encoding="utf-8"))
entities = payload.get("data", {{}}).get("entities", [])
station_ids = {station_ids!r}
counts = {{
    station_id: sum(
        1
        for entity in entities
        if entity.get("platform") == "{DOMAIN}"
        and str(entity.get("unique_id", "")).startswith(f"{{station_id}}_")
    )
    for station_id in station_ids
}}
print(json.dumps(counts, sort_keys=True))
"""
    result = _run(
        ["docker", "exec", container_name, "python", "-c", script],
        timeout=60,
        check=False,
        env=docker_env,
    )
    if result.returncode == 2:
        return None
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to inspect Home Assistant entity registry:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def _wait_for_live_entities(
    container_name: str,
    docker_env: dict[str, str],
    station_ids: list[str],
    timeout: int,
    *,
    allow_expected_upstream_errors: bool = False,
) -> dict[str, int]:
    """Wait until live config entries have produced entities for every station."""
    deadline = time.monotonic() + timeout
    last_counts: dict[str, int] | None = None
    last_logs = ""
    while time.monotonic() < deadline:
        last_logs = _combined_logs(container_name, docker_env)
        _assert_no_error_logs(
            last_logs,
            container_name,
            allow_expected_upstream_errors=allow_expected_upstream_errors,
        )
        last_counts = _entity_counts(container_name, docker_env, station_ids)
        if last_counts and all(last_counts.get(station_id, 0) > 0 for station_id in station_ids):
            return last_counts
        time.sleep(5)

    raise TimeoutError(
        "Timed out waiting for live station entities. "
        f"Last counts: {last_counts}\n"
        f"{_log_failure_context('live entity timeout', last_logs, container_name)}"
    )


def _wait_for_probe(
    container_name: str,
    docker_env: dict[str, str],
    timeout: int,
    *,
    allow_expected_upstream_errors: bool = False,
) -> dict[str, object]:
    """Wait for state assertions executed inside the Home Assistant process."""
    deadline = time.monotonic() + timeout
    last_logs = ""
    while time.monotonic() < deadline:
        last_logs = _combined_logs(container_name, docker_env)
        _assert_no_error_logs(
            last_logs,
            container_name,
            allow_expected_upstream_errors=allow_expected_upstream_errors,
        )
        result = _run(
            [
                "docker",
                "exec",
                container_name,
                "sh",
                "-c",
                "cat /config/docker-probe-result.json 2>/dev/null",
            ],
            check=False,
            env=docker_env,
        )
        if result.returncode == 0 and result.stdout.strip():
            payload = json.loads(result.stdout)
            if payload.get("status") != "passed":
                raise AssertionError(f"Home Assistant state probe failed: {payload}")
            return payload
        time.sleep(3)

    raise TimeoutError(
        "Timed out waiting for Home Assistant state probe.\n"
        f"{_log_failure_context('state probe timeout', last_logs, container_name)}"
    )


def _stop_container(container_name: str, docker_env: dict[str, str]) -> None:
    """Stop a test container, force-removing it if HA does not exit promptly."""
    try:
        result = _run(
            ["docker", "stop", container_name],
            timeout=30,
            check=False,
            env=docker_env,
        )
    except subprocess.TimeoutExpired:
        result = None
    if result is None or result.returncode != 0:
        _run(["docker", "rm", "-f", container_name], timeout=30, check=False, env=docker_env)


def _assert_persisted_profile(config_dir: Path) -> dict[str, int]:
    """Verify that the lived profile has data before Home Assistant starts."""
    storage_dir = config_dir / ".storage"
    paths = {
        "config_entries": storage_dir / "core.config_entries",
        "entity_registry": storage_dir / "core.entity_registry",
        "station_cache": storage_dir / f"{DOMAIN}_cache.json",
    }
    if any(not path.is_file() for path in paths.values()):
        missing = [name for name, path in paths.items() if not path.is_file()]
        raise AssertionError(f"Lived profile is missing persisted files: {missing}")

    config_entries = json.loads(paths["config_entries"].read_text(encoding="utf-8"))
    entity_registry = json.loads(paths["entity_registry"].read_text(encoding="utf-8"))
    station_cache = json.loads(paths["station_cache"].read_text(encoding="utf-8"))
    entry_count = sum(
        entry.get("domain") == DOMAIN
        for entry in config_entries.get("data", {}).get("entries", [])
    )
    entity_count = sum(
        entity.get("platform") == DOMAIN
        for entity in entity_registry.get("data", {}).get("entities", [])
    )
    station_count = len(station_cache.get("stations", {}))
    summary = {
        "config_entries": entry_count,
        "entity_registry_entries": entity_count,
        "cached_stations": station_count,
    }
    if not all(summary.values()):
        raise AssertionError(f"Lived profile has no usable persisted data: {summary}")
    return summary


def _assert_aged_profile(config_dir: Path) -> dict[str, int | float]:
    """Verify that the exported profile contains committed multi-day history."""
    marker_path = config_dir / "lived-profile-builder.json"
    database_path = config_dir / "home-assistant_v2.db"
    if not marker_path.is_file() or not database_path.is_file():
        raise AssertionError("Exported lived profile has no builder marker or Recorder database")

    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT date(last_updated_ts, 'unixepoch')),
                   MIN(last_updated_ts), MAX(last_updated_ts)
            FROM states
            """
        ).fetchone()
    summary = {
        "boots": int(marker.get("boot_count", 0)),
        "history_rows": int(row[0] or 0),
        "history_days": int(row[1] or 0),
        "history_span_days": round(float((row[3] or 0) - (row[2] or 0)) / 86400, 1),
    }
    if summary["boots"] < LIVED_BOOT_COUNT:
        raise AssertionError(f"Lived profile has too few real HA boots: {summary}")
    if summary["history_days"] < LIVED_HISTORY_DAYS - 1:
        raise AssertionError(f"Lived profile has too little Recorder history: {summary}")
    if summary["history_span_days"] < LIVED_HISTORY_DAYS - 2:
        raise AssertionError(f"Lived profile history is not spread across days: {summary}")
    return summary


def _checkpoint_recorder_database(config_dir: Path) -> None:
    """Checkpoint Recorder's SQLite WAL before copying the lived profile."""
    database_path = config_dir / "home-assistant_v2.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for suffix in ("-shm", "-wal"):
        (config_dir / f"home-assistant_v2.db{suffix}").unlink(missing_ok=True)


def _restore_config_ownership(
    mount_path: str,
    docker_env: dict[str, str],
) -> None:
    """Make root-written test files readable by the host-side runner."""
    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        return
    _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{mount_path}:/config",
            "alpine:latest",
            "chown",
            "-R",
            f"{os.getuid()}:{os.getgid()}",
            "/config",
        ],
        timeout=120,
        env=docker_env,
    )


def _run_builder_cycle(
    config_dir: Path,
    image: str,
    timeout: int,
    cycle: int,
    docker_env: dict[str, str],
) -> dict[str, object]:
    """Boot the isolated lived-profile builder once and collect its result."""
    mount_path = str(config_dir)
    if os.name == "nt":
        mount_path = mount_path.replace("\\", "/")
    result_path = config_dir / "docker-probe-result.json"
    result_path.unlink(missing_ok=True)
    container_name = f"ha-{DOMAIN}-builder-{cycle}-{uuid.uuid4().hex[:8]}"
    started = False
    try:
        _run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                container_name,
                "-v",
                f"{mount_path}:/config",
                image,
            ],
            timeout=120,
            env=docker_env,
        )
        started = True
        _wait_for_startup(container_name, timeout, docker_env)
        result = _wait_for_probe(container_name, docker_env, timeout)
        if result.get("boot_count") != cycle + 1:
            raise AssertionError(f"Builder boot count mismatch: {result}")
        return result
    finally:
        if started:
            _stop_container(container_name, docker_env)


def _build_lived_profile(
    repo_root: Path,
    output_dir: Path,
    image: str,
    timeout: int,
    station_ids: list[str],
    docker_env: dict[str, str],
    *,
    integration_root: Path | None = None,
) -> tuple[Path, dict[str, int], dict[str, int | float]]:
    """Generate, boot, export, and unpack a synthetic but real HA profile."""
    builder_dir = output_dir / "lived-builder"
    builder_dir.mkdir()
    _copy_integration(integration_root or repo_root, builder_dir)
    _copy_probe(repo_root, builder_dir)
    _write_ha_config(builder_dir, "builder", station_ids)

    last_result: dict[str, object] = {}
    for cycle in range(LIVED_BOOT_COUNT):
        last_result = _run_builder_cycle(builder_dir, image, timeout, cycle, docker_env)

    _restore_config_ownership(str(builder_dir), docker_env)
    _checkpoint_recorder_database(builder_dir)
    persisted_summary = _assert_persisted_profile(builder_dir)
    aged_summary = _assert_aged_profile(builder_dir)
    _checkpoint_recorder_database(builder_dir)
    archive_path = Path(shutil.make_archive(str(output_dir / "lived-profile"), "gztar", builder_dir))
    lived_dir = output_dir / "lived"
    lived_dir.mkdir()
    shutil.unpack_archive(archive_path, lived_dir)
    _write_ha_config(lived_dir, "lived", station_ids)
    (lived_dir / "docker-probe-result.json").unlink(missing_ok=True)
    print(f"Synthetic lived profile exported after {last_result.get('boot_count')} HA boots")
    print(f"Lived persisted data: {persisted_summary}")
    print(f"Lived Recorder history: {aged_summary}")
    print(f"Lived profile archive: {archive_path}")
    return lived_dir, persisted_summary, aged_summary


def _run_profile(
    config_dir: Path,
    profile: str,
    image: str,
    timeout: int,
    station_ids: list[str],
    docker_env: dict[str, str],
    container_names: list[str],
    keep: bool,
    blocked_hosts: list[str] | None = None,
) -> None:
    """Run the state probe against one exported or fresh profile."""
    _write_ha_config(config_dir, profile, station_ids)
    result_path = config_dir / "docker-probe-result.json"
    result_path.unlink(missing_ok=True)
    mount_path = str(config_dir)
    if os.name == "nt":
        mount_path = mount_path.replace("\\", "/")
    container_name = f"ha-{DOMAIN}-{profile}-{uuid.uuid4().hex[:8]}"
    container_names.append(container_name)
    started = False
    try:
        command = [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-v",
            f"{mount_path}:/config",
            image,
        ]
        for host in blocked_hosts or []:
            command[2:2] = ["--add-host", f"{host}:127.0.0.1"]
        _run(
            command,
            timeout=120,
            env=docker_env,
        )
        started = True
        allow_expected_upstream_errors = profile == "outage"
        logs = _wait_for_startup(
            container_name,
            timeout,
            docker_env,
            allow_expected_upstream_errors=allow_expected_upstream_errors,
        )
        _run_import_contract(container_name, docker_env)
        probe_result = _wait_for_probe(
            container_name,
            docker_env,
            timeout,
            allow_expected_upstream_errors=allow_expected_upstream_errors,
        )
        configured_values = probe_result.get("configured_station_ids")
        if not isinstance(configured_values, list):
            raise AssertionError(f"State probe returned invalid station IDs: {probe_result}")
        configured_station_ids = [str(station_id) for station_id in configured_values]
        if not configured_station_ids:
            raise AssertionError("State probe returned no configured stations")
        entity_counts = _wait_for_live_entities(
            container_name,
            docker_env,
            configured_station_ids,
            timeout,
            allow_expected_upstream_errors=allow_expected_upstream_errors,
        )
        discovery_result = _run_discovery_contract(container_name, docker_env)
        _assert_no_error_logs(
            _combined_logs(container_name, docker_env),
            container_name,
            allow_expected_upstream_errors=allow_expected_upstream_errors,
        )
        print(f"Home Assistant Docker regression passed: {profile}")
        print(f"Image: {image}")
        print(f"Container: {container_name}")
        print(f"State probe: {probe_result}")
        print(f"Live station entity counts: {entity_counts}")
        print(f"Live nearby discovery: {discovery_result}")
        print("Matched startup logs:")
        for line in logs.splitlines():
            if DOMAIN in line or STARTUP_OK_PATTERN.search(line):
                print(line)
    finally:
        if started and not (keep and profile == "recovery"):
            _stop_container(container_name, docker_env)


def run_regression(image: str, timeout: int, keep: bool, station_ids: list[str]) -> None:
    """Run the Docker regression workflow."""
    docker_env = os.environ.copy()
    docker_info = _run(["docker", "info"], timeout=120, check=False, env=docker_env)
    if docker_info.returncode != 0 and "v1.54/info" in docker_info.stderr:
        docker_env["DOCKER_API_VERSION"] = "1.44"
        docker_info = _run(["docker", "info"], timeout=120, check=False, env=docker_env)
    if docker_info.returncode != 0:
        raise RuntimeError(
            "Docker is installed but the daemon is not reachable. "
            "Start Docker Desktop or the Docker service, then rerun this script.\n"
            f"stderr:\n{docker_info.stderr}"
        )

    repo_root = Path(__file__).resolve().parents[1]
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{DOMAIN}_ha_"))
    container_names: list[str] = []
    try:
        previous_source = _extract_tagged_integration(
            repo_root, temp_dir / "previous-source", UPGRADE_FROM_TAG
        )
        previous_version = _integration_version(previous_source)
        current_version = _integration_version(repo_root)
        if previous_version == current_version:
            raise AssertionError(
                f"Upgrade profile is not testing a version change: {previous_version}"
            )
        upgrade_output = temp_dir / "upgrade"
        upgrade_output.mkdir()
        upgrade_dir, upgrade_persisted, upgrade_aged = _build_lived_profile(
            repo_root,
            upgrade_output,
            image,
            timeout,
            station_ids,
            docker_env,
            integration_root=previous_source,
        )
        _replace_integration(repo_root, upgrade_dir)
        _copy_probe(repo_root, upgrade_dir)
        _restore_config_ownership(str(upgrade_dir), docker_env)
        _assert_persisted_profile(upgrade_dir)
        _assert_aged_profile(upgrade_dir)
        print(f"Upgrade profile: {previous_version} -> {current_version}")
        _run_profile(
            upgrade_dir,
            "upgrade",
            image,
            timeout,
            station_ids,
            docker_env,
            container_names,
            keep,
        )
        lived_dir, persisted_summary, aged_summary = _build_lived_profile(
            repo_root, temp_dir, image, timeout, station_ids, docker_env
        )
        fresh_dir = temp_dir / "fresh"
        fresh_dir.mkdir()
        _copy_integration(repo_root, fresh_dir)
        _copy_probe(repo_root, fresh_dir)
        _run_profile(
            fresh_dir, "fresh", image, timeout, station_ids, docker_env, container_names, keep
        )
        _restore_config_ownership(str(lived_dir), docker_env)
        _assert_persisted_profile(lived_dir)
        _assert_aged_profile(lived_dir)
        _run_profile(
            lived_dir, "lived", image, timeout, station_ids, docker_env, container_names, keep
        )
        _restore_config_ownership(str(lived_dir), docker_env)
        _checkpoint_recorder_database(lived_dir)
        outage_dir = temp_dir / "outage"
        shutil.copytree(lived_dir, outage_dir)
        _write_ha_config(outage_dir, "outage", station_ids)
        (outage_dir / "docker-probe-result.json").unlink(missing_ok=True)
        _run_profile(
            outage_dir,
            "outage",
            image,
            timeout,
            station_ids,
            docker_env,
            container_names,
            keep,
            blocked_hosts=["carburanti.mise.gov.it", "www.mimit.gov.it"],
        )
        _restore_config_ownership(str(outage_dir), docker_env)
        _assert_persisted_profile(outage_dir)
        _assert_aged_profile(outage_dir)
        _run_profile(
            outage_dir,
            "recovery",
            image,
            timeout,
            station_ids,
            docker_env,
            container_names,
            keep,
        )
        print(f"Exported profile used for lived run: {lived_dir}")
        print(f"Lived profile before run: {persisted_summary}, {aged_summary}")
        print(f"Upgrade profile before run: {upgrade_persisted}, {upgrade_aged}")
    finally:
        if keep:
            if container_names:
                print(f"Kept container: {container_names[-1]}")
            print(f"Kept Home Assistant config: {temp_dir}")
        else:
            # ponytail: HA runs as root and writes root-owned files into the bind-mounted config dir
            # that the host user can't delete. ignore_errors keeps a passing regression from being
            # reported as a cleanup failure. Ceiling: root-owned temp dirs linger in /tmp when run
            # locally (CI runners are ephemeral). Reclaim with
            # `docker run --rm -v <dir>:/c alpine chown -R $(id -u):$(id -g) /c` if that matters.
            _restore_config_ownership(str(temp_dir), docker_env)
            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    """Parse arguments and run the regression."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Home Assistant Docker image to run")
    parser.add_argument("--timeout", default=180, type=int, help="Startup timeout in seconds")
    parser.add_argument("--keep", action="store_true", help="Keep the container running for inspection")
    parser.add_argument(
        "--station",
        action="append",
        dest="station_ids",
        help="Station ID to configure; repeat for multiple stations",
    )
    args = parser.parse_args()
    station_ids = args.station_ids or list(DEFAULT_STATION_IDS)

    try:
        run_regression(args.image, args.timeout, args.keep, station_ids)
    except Exception as err:
        print(f"Home Assistant Docker regression failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

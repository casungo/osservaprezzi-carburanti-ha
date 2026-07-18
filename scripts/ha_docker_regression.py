"""Run Home Assistant Docker smoke regressions for the custom integration."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
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


def _write_ha_config(config_dir: Path) -> None:
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
                f"{DOMAIN}:",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _copy_integration(repo_root: Path, config_dir: Path) -> None:
    """Copy the integration into Home Assistant's config directory."""
    source = repo_root / "custom_components" / DOMAIN
    destination = config_dir / "custom_components" / DOMAIN
    shutil.copytree(source, destination)


def _write_config_entries(config_dir: Path, station_ids: list[str]) -> None:
    """Write real Home Assistant config entries for live station setup."""
    now = datetime.now(timezone.utc).isoformat()
    entries = []
    for station_id in station_ids:
        entries.append(
            {
                "created_at": now,
                "data": {"station_id": station_id},
                "disabled_by": None,
                "discovery_keys": {},
                "domain": DOMAIN,
                "entry_id": uuid.uuid4().hex,
                "minor_version": 1,
                "modified_at": now,
                "options": {},
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "source": "user",
                "subentries": [],
                "title": f"Station {station_id}",
                "unique_id": f"station_{station_id}",
                "version": 2,
            }
        )

    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "core.config_entries").write_text(
        json.dumps(
            {
                "version": 1,
                "minor_version": 5,
                "key": "core.config_entries",
                "data": {"entries": entries},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


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


def _assert_no_error_logs(logs: str, container_name: str) -> None:
    """Fail if logs contain integration startup errors."""
    for pattern in ERROR_PATTERNS:
        if pattern.search(logs):
            context = _log_failure_context("integration log check", logs, container_name)
            raise AssertionError(
                f"Home Assistant logs contain an error matching {pattern.pattern}:\n{context}"
            )


def _wait_for_startup(container_name: str, timeout: int, docker_env: dict[str, str]) -> str:
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
        _assert_no_error_logs(last_logs, container_name)
        if STARTUP_OK_PATTERN.search(last_logs) and DOMAIN in last_logs:
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
) -> dict[str, int]:
    """Wait until live config entries have produced entities for every station."""
    deadline = time.monotonic() + timeout
    last_counts: dict[str, int] | None = None
    last_logs = ""
    while time.monotonic() < deadline:
        last_logs = _combined_logs(container_name, docker_env)
        _assert_no_error_logs(last_logs, container_name)
        last_counts = _entity_counts(container_name, docker_env, station_ids)
        if last_counts and all(last_counts.get(station_id, 0) > 0 for station_id in station_ids):
            return last_counts
        time.sleep(5)

    raise TimeoutError(
        "Timed out waiting for live station entities. "
        f"Last counts: {last_counts}\n"
        f"{_log_failure_context('live entity timeout', last_logs, container_name)}"
    )


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
    container_name = f"ha-{DOMAIN}-regression-{uuid.uuid4().hex[:8]}"

    temp_dir = tempfile.mkdtemp(prefix=f"{DOMAIN}_ha_")
    try:
        config_dir = Path(temp_dir)
        _copy_integration(repo_root, config_dir)
        _write_ha_config(config_dir)
        _write_config_entries(config_dir, station_ids)

        mount_path = str(config_dir)
        if os.name == "nt":
            mount_path = mount_path.replace("\\", "/")

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
            logs = _wait_for_startup(container_name, timeout, docker_env)
            _run_import_contract(container_name, docker_env)
            entity_counts = _wait_for_live_entities(container_name, docker_env, station_ids, timeout)
            _assert_no_error_logs(_combined_logs(container_name, docker_env), container_name)
            print("Home Assistant Docker regression passed")
            print(f"Image: {image}")
            print(f"Container: {container_name}")
            print(f"Live station entity counts: {entity_counts}")
            print("Matched startup logs:")
            for line in logs.splitlines():
                if DOMAIN in line or STARTUP_OK_PATTERN.search(line):
                    print(line)
        finally:
            if started and not keep:
                _run(["docker", "stop", container_name], timeout=30, check=False, env=docker_env)
    finally:
        # ponytail: HA runs as root and writes root-owned files into the bind-mounted config dir
        # that the host user can't delete. ignore_errors keeps a passing regression from being
        # reported as a cleanup failure. Ceiling: root-owned temp dirs linger in /tmp when run
        # locally (CI runners are ephemeral). Reclaim with
        # `docker run --rm -v <dir>:/c alpine chown -R $(id -u):$(id -g) /c` if that matters.
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

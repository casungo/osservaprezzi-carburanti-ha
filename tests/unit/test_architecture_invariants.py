"""Enforce repository and Home Assistant integration invariants in code."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

COMPONENT_ROOT = (
    Path(__file__).resolve().parents[2] / "custom_components" / "osservaprezzi_carburanti"
)
CORE_MANAGED_REQUIREMENTS = frozenset({"aiohttp", "cronsim", "homeassistant", "voluptuous"})
ENTITY_BASES = frozenset({"BinarySensorEntity", "ButtonEntity", "SensorEntity"})
BLOCKING_OS_ATTRS = frozenset(
    {"listdir", "makedirs", "remove", "replace", "scandir", "stat", "unlink", "walk"}
)
BLOCKING_PATH_ATTRS = frozenset(
    {"mkdir", "open", "read_bytes", "read_text", "stat", "unlink", "write_bytes", "write_text"}
)
BLOCKING_SSL_ATTRS = frozenset(
    {
        "load_cert_chain",
        "load_default_certs",
        "load_verify_locations",
        "set_default_verify_paths",
    }
)
FORBIDDEN_PROPERTY_ATTRS = (
    frozenset({"async_add_executor_job", "async_get_clientsession", "get_async_client"})
    | BLOCKING_PATH_ATTRS
)


def _component_trees() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(COMPONENT_ROOT.glob("*.py"))
    ]


def _walk_without_nested_sync_defs(node: ast.AST) -> list[ast.AST]:
    """Yield AST descendants inside an async function, skipping nested sync helpers."""
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.FunctionDef):
            continue
        nodes.append(current)
        stack.extend(ast.iter_child_nodes(current))
    return nodes


def _call_name(call: ast.Call) -> tuple[str | None, str | None]:
    if isinstance(call.func, ast.Name):
        return None, call.func.id
    if isinstance(call.func, ast.Attribute):
        owner = call.func.value.id if isinstance(call.func.value, ast.Name) else None
        return owner, call.func.attr
    return None, None


def test_manifest_invariants() -> None:
    """Enforce stable integration metadata and forbid pinning HA Core dependencies."""
    manifest = json.loads((COMPONENT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["domain"] == "osservaprezzi_carburanti"
    assert manifest["integration_type"] == "service"
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["config_flow"] is True

    for requirement in manifest.get("requirements", []):
        package = re.split(r"[<>=!~;\[\s]", str(requirement), maxsplit=1)[0].strip().lower()
        assert package not in CORE_MANAGED_REQUIREMENTS, (
            f"Do not declare Home Assistant Core dependency {package!r} in manifest.json "
            "(for example, cronsim is provided by HA Core)."
        )


def test_component_modules_use_standard_logger_and_public_docstrings() -> None:
    """Enforce _LOGGER = logging.getLogger(__name__) and docstrings on public symbols."""
    for path, tree in _component_trees():
        for node in tree.body:
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not node.name.startswith("_"):
                assert ast.get_docstring(node), (
                    f"Missing docstring on public {type(node).__name__} {path.name}:{node.lineno} "
                    f"({node.name})"
                )

            if not isinstance(node, ast.Assign):
                continue
            if isinstance(node.value, ast.Call) and _call_name(node.value) == (
                "logging",
                "getLogger",
            ):
                is_standard_logger = (
                    len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "_LOGGER"
                    and len(node.value.args) == 1
                    and isinstance(node.value.args[0], ast.Name)
                    and node.value.args[0].id == "__name__"
                )
                assert is_standard_logger, (
                    f"Use `_LOGGER = logging.getLogger(__name__)` in {path.name}:{node.lineno}"
                )


def test_no_raw_aiohttp_client_session_in_component() -> None:
    """All HTTP calls must reuse Home Assistant's managed client session."""
    for path, tree in _component_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                _, attr = _call_name(node)
                assert attr != "ClientSession", (
                    f"Do not instantiate ClientSession directly in {path.name}:{node.lineno}; "
                    "use async_get_clientsession(hass)."
                )


def test_no_blocking_io_in_async_component_functions() -> None:
    """Forbid blocking file, sleep, network, and SSL calls directly inside async functions."""
    violations: list[str] = []
    for path, tree in _component_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for child in _walk_without_nested_sync_defs(node):
                if not isinstance(child, ast.Call):
                    continue
                owner, attr = _call_name(child)
                if owner is None and attr == "open":
                    violations.append(f"{path.name}:{child.lineno} open() in {node.name}")
                elif owner == "time" and attr == "sleep":
                    violations.append(f"{path.name}:{child.lineno} time.sleep() in {node.name}")
                elif owner in {"urllib", "requests"}:
                    violations.append(f"{path.name}:{child.lineno} {owner}.{attr}() in {node.name}")
                elif owner == "os" and attr in BLOCKING_OS_ATTRS:
                    violations.append(f"{path.name}:{child.lineno} os.{attr}() in {node.name}")
                elif owner == "glob" and attr in {"glob", "iglob"}:
                    violations.append(f"{path.name}:{child.lineno} glob.{attr}() in {node.name}")
                elif (
                    attr in BLOCKING_PATH_ATTRS and owner not in {"asyncio", "dt_util"}
                ) or attr in BLOCKING_SSL_ATTRS:
                    violations.append(f"{path.name}:{child.lineno} .{attr}() in {node.name}")
    assert not violations, "Blocking call(s) found inside async functions:\n" + "\n".join(
        violations
    )


def test_concrete_entities_set_has_entity_name() -> None:
    """Every concrete HA entity class must opt into modern has_entity_name naming."""
    for path, tree in _component_trees():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {
                base.id if isinstance(base, ast.Name) else base.attr
                for base in node.bases
                if isinstance(base, (ast.Name, ast.Attribute))
            }
            if not (base_names & ENTITY_BASES):
                continue
            has_entity_name = any(
                isinstance(stmt, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "_attr_has_entity_name"
                    for target in stmt.targets
                )
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is True
                for stmt in node.body
            )
            assert has_entity_name, (
                f"Entity class {node.name} in {path.name}:{node.lineno} must set "
                "`_attr_has_entity_name = True`."
            )


def test_entity_properties_do_no_io_or_executor_calls() -> None:
    """Entity properties must stay synchronous, cheap, and free of I/O."""
    for filename in ("entity.py", "sensor.py", "binary_sensor.py", "button.py"):
        path = COMPONENT_ROOT / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for item in cls.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                is_property = any(
                    isinstance(dec, ast.Name) and dec.id == "property"
                    for dec in item.decorator_list
                )
                if not is_property:
                    continue
                for child in ast.walk(item):
                    if not isinstance(child, ast.Call):
                        continue
                    owner, attr = _call_name(child)
                    assert not (owner is None and attr == "open"), (
                        f"Blocking open() in property {cls.name}.{item.name} ({path.name}:{child.lineno})"
                    )
                    assert attr not in FORBIDDEN_PROPERTY_ATTRS, (
                        f"I/O call .{attr}() in property {cls.name}.{item.name} "
                        f"({path.name}:{child.lineno})"
                    )


def test_global_services_only_registered_in_services_module() -> None:
    """Service registration/removal belongs in services.py, never in platform modules."""
    for path, tree in _component_trees():
        if path.name == "services.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                _, attr = _call_name(node)
                assert attr not in {"async_register", "async_remove"} or (
                    isinstance(node.func, ast.Attribute)
                    and not (
                        isinstance(node.func.value, ast.Attribute)
                        and node.func.value.attr == "services"
                    )
                ), f"Register/remove services only in services.py, not {path.name}:{node.lineno}"

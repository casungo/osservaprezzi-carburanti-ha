from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_DIR = REPO_ROOT / "blueprints/automation/osservaprezzi_carburanti"
COMPONENT_DIR = REPO_ROOT / "custom_components/osservaprezzi_carburanti"
DOMAIN = "osservaprezzi_carburanti"


@dataclass(frozen=True)
class InputReference:
    name: str


class BlueprintLoader(yaml.SafeLoader):
    """Preserve Home Assistant input references while loading YAML safely."""


def _construct_input(loader: yaml.SafeLoader, node: yaml.Node) -> InputReference:
    assert isinstance(node, yaml.ScalarNode)
    return InputReference(loader.construct_scalar(node))


BlueprintLoader.add_constructor("!input", _construct_input)


def _walk(value: Any) -> Any:
    """Visit nested mappings, keys, and sequences."""
    yield value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(key)
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_blueprints_are_consistent() -> None:
    paths = sorted(path for path in BLUEPRINT_DIR.rglob("*") if path.suffix in {".yaml", ".yml"})
    assert paths, "No automation blueprints found"
    services = yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8"))
    constants = ast.parse((COMPONENT_DIR / "const.py").read_text(encoding="utf-8"))
    events = {
        node.value.value
        for node in constants.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id.startswith("EVENT_")
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    for path in paths:
        blueprint = yaml.load(path.read_text(encoding="utf-8"), Loader=BlueprintLoader)  # noqa: S506
        assert isinstance(blueprint, dict), path
        metadata = blueprint.get("blueprint")
        assert isinstance(metadata, dict), path
        assert metadata.get("domain") == "automation", path
        for field in ("name", "description"):
            assert isinstance(metadata.get(field), str), (path, field)
            assert metadata[field].strip(), (path, field)
        inputs = metadata.get("input")
        assert isinstance(inputs, dict), path

        for item in _walk(blueprint):
            if isinstance(item, InputReference):
                assert item.name in inputs, (path, item.name)
            if not isinstance(item, dict):
                continue
            for key in ("action", "service"):
                service = item.get(key)
                if isinstance(service, str) and service.startswith(f"{DOMAIN}."):
                    service_name = service.removeprefix(f"{DOMAIN}.")
                    assert service_name in services, (path, service)
                    service_data = item.get("data", {})
                    assert isinstance(service_data, dict), (path, service)
                    unknown_fields = (
                        service_data.keys() - services[service_name].get("fields", {}).keys()
                    )
                    assert not unknown_fields, (path, service, unknown_fields)
            event_type = item.get("event_type")
            if isinstance(event_type, str) and event_type.startswith(f"{DOMAIN}_"):
                assert event_type in events, (path, event_type)

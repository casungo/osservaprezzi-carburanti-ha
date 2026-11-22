from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from string import Formatter

import pytest

ROOT = Path(__file__).resolve().parents[2] / "custom_components" / "osservaprezzi_carburanti"
FILES = (ROOT / "strings.json", ROOT / "translations/en.json", ROOT / "translations/it.json")


def leaves(value: object, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], object]:
    if isinstance(value, dict):
        return {
            key: leaf
            for name, child in value.items()
            for key, leaf in leaves(child, (*path, name)).items()
        }
    return {path: value}


def translations() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in FILES]


def test_translation_leaves_and_placeholders() -> None:
    documents = [leaves(document) for document in translations()]
    expected = documents[0]
    for path, document in zip(FILES, documents, strict=True):
        assert document.keys() == expected.keys(), f"Translation keys differ in {path}"
        for key, value in document.items():
            assert isinstance(value, str), f"Invalid translation {path}: {key}"
            assert value.strip(), f"Empty translation {path}: {key}"
            placeholders = {name for _, name, _, _ in Formatter().parse(value) if name is not None}
            original = {
                name for _, name, _, _ in Formatter().parse(expected[key]) if name is not None
            }
            assert placeholders == original, f"Placeholder mismatch in {path}: {key}"


def parse_service_names(text: str) -> dict[str, set[str]]:
    """Read service and field names from the repository's indentation-based YAML."""
    services: dict[str, set[str]] = {}
    service = None
    in_fields = False
    for line in text.splitlines():
        if match := re.fullmatch(r"([a-z_][a-z_0-9]*):\s*(?:#.*)?", line):
            service = match.group(1)
            services[service] = set()
            in_fields = False
        elif re.fullmatch(r"  fields:\s*(?:#.*)?", line):
            assert service is not None
            in_fields = True
        elif re.match(r"  \S", line):
            in_fields = False
        elif in_fields and (match := re.fullmatch(r"    ([a-z_][a-z_0-9]*):\s*(?:#.*)?", line)):
            assert service is not None
            services[service].add(match.group(1))
    assert services, "No services found in services.yaml"
    return services


def test_service_field_parity() -> None:
    text = (ROOT / "services.yaml").read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        services = parse_service_names(text)
    else:
        services = {
            name: set(spec.get("fields", {})) for name, spec in yaml.safe_load(text).items()
        }
        assert parse_service_names(text) == services
    translated = translations()[0]["services"]
    assert services.keys() == translated.keys()
    for name, fields in services.items():
        assert fields == set(translated[name].get("fields", {})), name


def test_static_translation_keys() -> None:
    document = translations()[0]
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg != "translation_key":
                        continue
                    value = keyword.value
                    key = (
                        value.value
                        if isinstance(value, ast.Constant)
                        else constants.get(value.id)
                        if isinstance(value, ast.Name)
                        else None
                    )
                    if not isinstance(key, str):
                        continue
                    function = (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else node.func.id
                        if isinstance(node.func, ast.Name)
                        else ""
                    )
                    if function in {"async_create_issue", "create_issue"}:
                        section = document["issues"]
                    elif function.endswith("SelectorConfig"):
                        section = document["selector"]
                    elif function.endswith("EntityDescription"):
                        section = document["entity"][path.stem]
                    else:
                        section = document["exceptions"]
                    assert key in section, f"{path.name}:{node.lineno}: {key}"
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "_attr_translation_key"
                        ) or (
                            isinstance(target, ast.Name) and target.id == "_attr_translation_key"
                        ):
                            assert value.value in document["entity"][path.stem], (
                                f"{path.name}:{node.lineno}: {value.value}"
                            )


def test_service_entity_translation_keys() -> None:
    tree = ast.parse((ROOT / "const.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SERVICE_ID_TO_TRANSLATION_KEY"
            for target in node.targets
        ):
            mapping = ast.literal_eval(node.value)
            assert set(mapping.values()) <= translations()[0]["entity"]["binary_sensor"].keys()
            return
    pytest.fail("SERVICE_ID_TO_TRANSLATION_KEY not found")

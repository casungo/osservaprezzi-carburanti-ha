"""Validate repository JSON and YAML files, including Home Assistant tags."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "artifacts",
    }
)


class _HASafeLoader(yaml.SafeLoader):
    """YAML SafeLoader that accepts Home Assistant custom tags such as !input."""


def _construct_ha_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    """Return a tagged scalar, sequence, or mapping without evaluating arbitrary code."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


_HASafeLoader.add_multi_constructor("!", _construct_ha_tag)


def _is_ignored_part(part: str) -> bool:
    """Return True if a path segment belongs to an ignored directory."""
    return part in IGNORED_DIRS or part.startswith(".venv")


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Return sorted repository files matching suffixes, skipping ignored directories."""
    matched: list[Path] = []
    for path in root.rglob("*"):
        if any(_is_ignored_part(part) for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix in suffixes:
            matched.append(path)
    return sorted(matched)


def test_repository_json_files_are_valid() -> None:
    """Validate all JSON files in the repository."""
    json_files = _iter_files(REPO_ROOT, (".json",))
    assert json_files
    errors: list[str] = []
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            errors.append(f"{path.relative_to(REPO_ROOT)}: {err}")
    assert not errors, "Invalid JSON file(s):\n" + "\n".join(errors)


def test_repository_yaml_files_are_valid() -> None:
    """Validate all YAML files in the repository, including Home Assistant tags."""
    yaml_files = _iter_files(REPO_ROOT, (".yaml", ".yml"))
    assert yaml_files
    errors: list[str] = []
    for path in yaml_files:
        try:
            yaml.load(path.read_text(encoding="utf-8"), Loader=_HASafeLoader)  # noqa: S506
        except (OSError, yaml.YAMLError) as err:
            errors.append(f"{path.relative_to(REPO_ROOT)}: {err}")
    assert not errors, "Invalid YAML file(s):\n" + "\n".join(errors)

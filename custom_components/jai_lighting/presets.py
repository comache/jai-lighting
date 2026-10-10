"""Absolute hierarchical recipes: deepest assignment wins per property."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .capabilities import absolute, properties, validate_effective
from .models import ModelError, Observation, Plan, RuntimeState
from .topology import Topology


@dataclass(frozen=True)
class Target:
    kind: str
    id: str
    properties: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.kind not in {"group", "leaf"} or not self.id:
            raise ModelError("Invalid recipe target")
        object.__setattr__(self, "properties", MappingProxyType(properties(dict(self.properties))))


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    targets: tuple[Target, ...]

    def __post_init__(self) -> None:
        if not self.id or not self.name.strip() or not self.targets:
            raise ModelError("Preset requires identity, name and targets")


@dataclass(frozen=True)
class Source:
    kind: str
    id: str
    depth: int


@dataclass
class Effective:
    values: dict[str, dict[str, Any]]
    sources: dict[str, dict[str, Source]]


class PresetError(ModelError):
    def __init__(
        self,
        message: str,
        leaf: str | None = None,
        values: dict | None = None,
        sources: dict | None = None,
    ) -> None:
        self.leaf = leaf
        self.values = values or {}
        self.sources = sources or {}
        super().__init__(f"{message}; leaf={leaf}; effective={self.values}; sources={self.sources}")


def expand(tree: Topology, target: Target) -> tuple[tuple[str, ...], int]:
    if target.kind == "group":
        return tree.resolve(target.id), len(tree.ancestors(target.id))
    if target.id not in tree.leaves:
        raise ModelError(f"Missing preset leaf: {target.id}")
    return (target.id,), len(tree.leaf_path(target.id))


def resolve(tree: Topology, preset: Preset, *, inheritance: bool = True) -> Effective:
    # Validate duplicate authored assignments before any leaf-level shadowing.
    authored: dict[tuple[str, str, str], Any] = {}
    for target in preset.targets:
        expand(tree, target)
        for key, value in target.properties.items():
            identity = target.kind, target.id, key
            if identity in authored and authored[identity] != value:
                raise PresetError(f"Same-specificity conflict at {identity}")
            authored[identity] = value
    result = Effective({}, {})
    for target in preset.targets:
        leaves, depth = expand(tree, target)
        source = Source(target.kind, target.id, depth)
        for leaf in leaves:
            values = result.values.setdefault(leaf, {})
            sources = result.sources.setdefault(leaf, {})
            for key, value in target.properties.items():
                prior = sources.get(key)
                if prior is not None and not inheritance and values[key] != value:
                    raise PresetError("Conflicting non-preset batch", leaf, values, sources)
                if prior is None or source.depth > prior.depth:
                    values[key], sources[key] = value, source
    for leaf, values in result.values.items():
        try:
            validate_effective(values)
        except ModelError as err:
            raise PresetError(str(err), leaf, values, result.sources[leaf]) from err
    return result


def plan(
    tree: Topology, state: RuntimeState, observations: dict[str, Observation], preset: Preset
) -> tuple[Plan, Effective]:
    effective = resolve(tree, preset)
    return absolute(state, observations, effective.values), effective

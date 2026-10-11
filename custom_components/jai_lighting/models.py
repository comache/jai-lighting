"""HA-independent immutable lighting records and bounded runtime state."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ModelError(ValueError):
    """Invalid configuration or command; no physical work should follow."""


class PowerPolicy(StrEnum):
    RESTORE = "RESTORE"
    FULL = "FULL"


@dataclass(frozen=True)
class Identity:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind not in {"registry", "entity_id_fallback"}
            or not isinstance(self.value, str)
            or not self.value
        ):
            raise ModelError("Invalid physical identity")
        if self.kind == "entity_id_fallback" and not self.value.startswith("light."):
            raise ModelError("Only light entities can be members")

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.value


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    parent: str | None = None
    policy: PowerPolicy = PowerPolicy.RESTORE
    order: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not self.id
            or not isinstance(self.name, str)
            or not self.name.strip()
            or type(self.order) is not int
        ):
            raise ModelError("Invalid group")
        object.__setattr__(self, "policy", PowerPolicy(self.policy))


@dataclass(frozen=True)
class Leaf:
    id: str
    identity: Identity
    parent: str | None
    order: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not self.id
            or (self.parent is not None and (not isinstance(self.parent, str) or not self.parent))
            or type(self.order) is not int
        ):
            raise ModelError("Invalid leaf")


@dataclass(frozen=True)
class Capabilities:
    modes: frozenset[str] = frozenset()
    min_kelvin: int | None = None
    max_kelvin: int | None = None
    effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.modes, frozenset) or any(not isinstance(m, str) for m in self.modes):
            raise ModelError("Invalid capability modes")
        if self.modes - {
            "onoff",
            "brightness",
            "hs",
            "rgb",
            "xy",
            "rgbw",
            "rgbww",
            "color_temp",
            "white",
            "unknown",
        }:
            raise ModelError("Unknown capability mode")
        if any(
            v is not None and (type(v) is not int or v <= 0)
            for v in (self.min_kelvin, self.max_kelvin)
        ):
            raise ModelError("Invalid temperature bounds")
        if self.min_kelvin and self.max_kelvin and self.min_kelvin > self.max_kelvin:
            raise ModelError("Reversed temperature bounds")
        if any(not isinstance(e, str) or not e for e in self.effects):
            raise ModelError("Invalid effect names")

    @property
    def dimmable(self) -> bool:
        return bool(self.modes - {"onoff", "unknown"})

    @property
    def color(self) -> bool:
        return bool(self.modes & {"hs", "rgb", "xy", "rgbw", "rgbww"})

    @property
    def temperature(self) -> bool:
        return (
            "color_temp" in self.modes
            and type(self.min_kelvin) is int
            and type(self.max_kelvin) is int
            and 0 < self.min_kelvin <= self.max_kelvin
        )


@dataclass(frozen=True)
class Observation:
    available: bool = False
    on: bool = False
    brightness: int | None = None
    capabilities: Capabilities = Capabilities()
    properties: Mapping[str, Any] = field(default_factory=dict)
    reason: str = "unavailable"

    def __post_init__(self) -> None:
        if self.brightness is not None and (
            type(self.brightness) is not int or not 1 <= self.brightness <= 255
        ):
            raise ModelError("Invalid observed brightness")
        object.__setattr__(self, "properties", MappingProxyType(dict(self.properties)))


@dataclass
class RuntimeState:
    masters: dict[str, int] = field(default_factory=dict)
    targets: dict[str, int] = field(default_factory=dict)

    def copy(self) -> RuntimeState:
        return RuntimeState(dict(self.masters), dict(self.targets))

    def validate(self) -> None:
        if any(type(v) is not int or not 3 <= v <= 255 for v in self.masters.values()):
            raise ModelError("Invalid retained master")
        if any(type(v) is not int or not 1 <= v <= 255 for v in self.targets.values()):
            raise ModelError("Invalid retained brightness")


@dataclass(frozen=True)
class Intention:
    leaf_id: str
    on: bool
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass
class Plan:
    state: RuntimeState
    commands: dict[str, Intention] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    clamps: dict[str, dict[str, int]] = field(default_factory=dict)

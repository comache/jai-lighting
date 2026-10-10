"""Pure capability aggregation and sparse compatible-only absolute planning."""

import math
from dataclasses import dataclass
from typing import Any

from .engine import level
from .models import Capabilities, Intention, ModelError, Observation, Plan, RuntimeState
from .planner import bootstrap, observe


@dataclass(frozen=True)
class Color:
    kind: str
    value: tuple[float | int, ...]


def properties(data: dict[str, Any]) -> dict[str, Any]:
    if not data or data.keys() - {
        "power",
        "brightness_pct",
        "color_temp_kelvin",
        "color",
        "effect",
    }:
        raise ModelError("Empty recipe or unknown properties")
    result = dict(data)
    if "power" in data and data["power"] not in {"on", "off"}:
        raise ModelError("Power must be on/off or omitted")
    if "brightness_pct" in data:
        value = data["brightness_pct"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 100:
            raise ModelError("Absolute brightness must be finite and in (0,100]")
    if "color_temp_kelvin" in data:
        value = data["color_temp_kelvin"]
        if type(value) is not int or value <= 0:
            raise ModelError("Temperature must be positive integer Kelvin")
    if "effect" in data and (not isinstance(data["effect"], str) or not data["effect"]):
        raise ModelError("Effect must be an advertised name")
    if "color" in data:
        color = data["color"]
        if not isinstance(color, Color):
            if not isinstance(color, dict) or set(color) != {"kind", "value"}:
                raise ModelError("Color must contain kind and value")
            color = Color(color["kind"], tuple(color["value"]))
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in color.value):
            raise ModelError("Non-finite color")
        if color.kind == "hs":
            valid = (
                len(color.value) == 2
                and 0 <= color.value[0] <= 360
                and (0 <= color.value[1] <= 100)
            )
        elif color.kind == "rgb":
            valid = (
                len(color.value) == 3
                and all(type(v) is int and 0 <= v <= 255 for v in color.value)
                and max(color.value) > 0
            )
        elif color.kind == "xy":
            valid = (
                len(color.value) == 2
                and 0 <= color.value[0] <= 1
                and (0 < color.value[1] <= 1 and sum(color.value) <= 1)
            )
        else:
            valid = False
        if not valid:
            raise ModelError("Invalid tagged color")
        result["color"] = color
    return result


def validate_effective(data: dict[str, Any]) -> None:
    if data.get("power") == "off" and set(data) != {"power"}:
        raise ModelError("OFF cannot combine with light properties")
    if "color" in data and "color_temp_kelvin" in data:
        raise ModelError("Color and CT are mutually exclusive")
    if "effect" in data and ("color" in data or "color_temp_kelvin" in data):
        raise ModelError("Effect cannot combine with color/CT")


def aggregate(caps: list[Capabilities]) -> dict[str, Any]:
    color = any(c.color for c in caps)
    temperature = [c for c in caps if c.temperature]
    modes = set()
    if color:
        modes.add("hs")
    if temperature:
        modes.add("color_temp")
    if not modes:
        modes.add("brightness" if any(c.dimmable for c in caps) else "onoff")
    return {
        "modes": modes,
        "min_kelvin": min((c.min_kelvin for c in temperature), default=None),
        "max_kelvin": max((c.max_kelvin for c in temperature), default=None),
        "effects": sorted({e for c in caps for e in c.effects}),
        "coverage": {
            "total": len(caps),
            "brightness": sum(c.dimmable for c in caps),
            "color": sum(c.color for c in caps),
            "temperature": len(temperature),
            "effects": sum(bool(c.effects) for c in caps),
        },
    }


def absolute(
    state: RuntimeState,
    observations: dict[str, Observation],
    effective: dict[str, dict[str, Any]],
) -> Plan:
    result = Plan(state.copy())
    # Semantic validity precedes both availability and capability filtering.
    for data in effective.values():
        validate_effective(data)
    observe(result.state, observations)
    for leaf_id, data in effective.items():
        o = observations.get(leaf_id, Observation())
        if not o.available:
            result.skipped[leaf_id] = ["unavailable"]
            continue
        bootstrap(result.state, leaf_id, o)
        if "power" not in data and not o.on:
            result.skipped[leaf_id] = ["off_power_preserved"]
            continue
        fields, skipped = {}, []
        for key, value in data.items():
            if key == "power":
                continue
            if key == "brightness_pct" and o.capabilities.dimmable:
                fields["brightness"] = level(value * 255 / 100)
            elif key == "color_temp_kelvin" and o.capabilities.temperature:
                applied = max(o.capabilities.min_kelvin, min(o.capabilities.max_kelvin, value))
                fields[key] = applied
                if applied != value:
                    result.clamps[leaf_id] = {"requested": value, "applied": applied}
            elif key == "color" and o.capabilities.color:
                # Native format conversion belongs to the HA adapter.
                modes = o.capabilities.modes
                mode = (
                    value.kind
                    if value.kind in modes
                    else next(m for m in ("hs", "rgb", "xy", "rgbw", "rgbww") if m in modes)
                )
                if mode == "rgbww" and not (
                    type(o.capabilities.min_kelvin) is int
                    and type(o.capabilities.max_kelvin) is int
                    and 0 < o.capabilities.min_kelvin <= o.capabilities.max_kelvin
                ):
                    skipped.append("color:missing_conversion_bounds")
                else:
                    fields["color"] = value
                    fields["color_mode"] = mode
            elif key == "effect" and value in o.capabilities.effects:
                fields[key] = value
            else:
                skipped.append(f"{key}:unsupported")
        if skipped:
            result.skipped[leaf_id] = skipped
        if fields or "power" in data:
            result.commands[leaf_id] = Intention(leaf_id, data.get("power", "on") == "on", fields)
    return result

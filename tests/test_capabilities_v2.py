"""Compatible-only sparse fields, legal mode aggregation, and power preservation."""

import pytest

from custom_components.jai_lighting.capabilities import absolute, aggregate, properties
from custom_components.jai_lighting.models import (
    Capabilities,
    ModelError,
    Observation,
    RuntimeState,
)


def test_union_modes_effects_and_temperature_envelope():
    caps = [
        Capabilities(frozenset({"onoff"})),
        Capabilities(frozenset({"hs"})),
        Capabilities(frozenset({"color_temp"}), 3000, 6000, ("pulse",)),
        Capabilities(frozenset({"color_temp"}), 2000, 2500),
    ]
    result = aggregate(caps)
    assert result["modes"] == {"hs", "color_temp"}
    assert (result["min_kelvin"], result["max_kelvin"]) == (2000, 6000)
    assert result["effects"] == ["pulse"]
    assert result["coverage"]["brightness"] == 3
    assert aggregate([caps[0]])["modes"] == {"onoff"}


def test_mixed_temperature_clamp_and_unsupported_skip():
    observations = {
        "ct": Observation(True, True, 90, Capabilities(frozenset({"color_temp"}), 3000, 6000)),
        "binary": Observation(True, True, None, Capabilities(frozenset({"onoff"}))),
    }
    data = properties({"color_temp_kelvin": 2700, "brightness_pct": 30})
    plan = absolute(RuntimeState(), observations, dict.fromkeys(observations, data))
    assert plan.commands["ct"].fields == {"brightness": 77, "color_temp_kelvin": 3000}
    assert plan.clamps["ct"] == {"requested": 2700, "applied": 3000}
    assert "binary" not in plan.commands
    assert len(plan.skipped["binary"]) == 2


def test_sparse_color_preserves_brightness_and_off_power():
    caps = Capabilities(frozenset({"rgb"}))
    data = properties({"color": {"kind": "hs", "value": [120, 50]}})
    plan = absolute(
        RuntimeState({"group": 128}),
        {
            "on": Observation(True, True, 80, caps),
            "off": Observation(True, False, 80, caps),
        },
        {"on": data, "off": data},
    )
    assert "brightness" not in plan.commands["on"].fields
    assert plan.commands["on"].fields["color_mode"] == "rgb"
    assert plan.skipped["off"] == ["off_power_preserved"]
    assert plan.state.masters == {"group": 128}


def test_effect_exact_names_and_rgbww_bounds():
    data = properties({"power": "on", "effect": "Pulse"})
    plan = absolute(
        RuntimeState(),
        {"one": Observation(True, True, 80, Capabilities(frozenset({"hs"}), effects=("pulse",)))},
        {"one": data},
    )
    assert plan.commands["one"].fields == {}
    assert plan.skipped["one"] == ["effect:unsupported"]
    color = properties({"color": {"kind": "rgb", "value": [255, 0, 0]}})
    plan = absolute(
        RuntimeState(),
        {"one": Observation(True, True, 80, Capabilities(frozenset({"rgbww"})))},
        {"one": color},
    )
    assert plan.skipped["one"] == ["color:missing_conversion_bounds"]


@pytest.mark.parametrize(
    "data",
    [
        {"brightness_pct": 0},
        {"brightness_pct": float("nan")},
        {"power": None},
        {"color": {"kind": "rgb", "value": [0, 0, 0]}},
        {"color": {"kind": "xy", "value": [0.8, 0.8]}},
    ],
)
def test_invalid_absolute_values(data):
    with pytest.raises(ModelError):
        properties(data)
